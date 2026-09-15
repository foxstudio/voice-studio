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
    asr_research_evidence_execution as execution,
    asr_research_evidence_managed_contracts as managed_contracts,
    asr_research_evidence_result_reader as result_reader,
    asr_research_evidence_search_gateway as search_gateway,
    managed_local_step,
    research_evidence,
)
from app.domains.video_localization.operation_detail_errors import (  # noqa: E402
    OperationDetailRepairRequired,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_research_evidence_step import (  # noqa: E402
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.voice_studio import WebSearchSettings  # noqa: E402
from app.services import database, llm_runtime, web_search  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
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
                ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
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


def _settings(*, paid: bool = False) -> WebSearchSettings:
    return WebSearchSettings(
        enabled=True,
        provider="tavily" if paid else "wikipedia",
        max_results_per_query=5,
        api_key_configured=paid,
    )


def _prepared(
    *,
    profile: llm_runtime.ResolvedProfile,
    settings: WebSearchSettings,
) -> managed_contracts.AsrResearchEvidencePreparedInputV1:
    request = research_evidence.AsrResearchEvidenceInput(
        upstream_operation_id="document-operation",
        source_track_id="vocals",
        source_audio_sha256="e" * 64,
        language="en",
        scene_context="AI investment interview",
        profile_id=profile.profile_id,
        document_summary="The guest discusses AI chip investing.",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2_000,
                "text": "JoAnne Feeney discusses AI chips.",
            }
        ],
        candidates=[
            {
                "candidate_id": "candidate_01",
                "query": "JoAnne Feeney AI chips interview",
                "category": "proper_noun",
                "reason": "Verify the guest name.",
                "target_terms": ["JoAnne Feeney"],
            }
        ],
        policy={"max_rounds": 2, "max_total_queries": 5},
    )
    return managed_contracts.AsrResearchEvidencePreparedInputV1(
        document_artifact_fingerprint="a" * 64,
        profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
        ),
        search_configuration_fingerprint=(
            search_gateway.search_configuration_fingerprint(settings)
        ),
        behavior_fingerprint=(
            execution.research_evidence_behavior_fingerprint()
        ),
        request=request,
    )


def _trace(sink, profile, *, response_id: str = "research-1"):
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host="127.0.0.1",
            request_chars=100,
            request_body_bytes=200,
            max_tokens=2_400,
            timeout_seconds=60,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason="stop",
            content_chars=40,
            reasoning_chars=0,
            response_id=response_id,
        )
    )


def test_managed_research_replays_search_call_and_final_manifest(
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
    settings = _settings()
    prepared = _prepared(profile=profile, settings=settings)
    search_calls = 0
    llm_calls = 0

    def search(_settings, query, *, api_key=None):
        nonlocal search_calls
        search_calls += 1
        assert api_key is None
        assert query == "JoAnne Feeney AI chips interview"
        return [
            web_search.SearchResult(
                title="JoAnne Feeney discusses AI chip stocks",
                url="https://example.com/interview",
                snippet=(
                    "Portfolio manager JoAnne Feeney discusses AI chips."
                ),
            )
        ]

    def complete_json(**kwargs):
        nonlocal llm_calls
        llm_calls += 1
        _trace(kwargs["trace_sink"], profile)
        return {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "sufficient",
                    "reason": "The public source directly matches.",
                    "followup_query": None,
                }
            ]
        }

    monkeypatch.setattr(web_search, "search", search)
    first = execution.execute_prepared_research_evidence(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        search_settings=settings,
        search_api_key=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    replay = execution.execute_prepared_research_evidence(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        search_settings=settings,
        search_api_key=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert first == replay
    assert first.status == "completed"
    assert first.stop_reason == "evidence_sufficient"
    assert first.stage_timing.duration_ms >= 25
    assert search_calls == 1
    assert llm_calls == 1
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert all(item.status == "success" for item in steps)
    step_ids = {item.step_id for item in steps}
    assert {
        "prepare_research_input",
        "finalize_research_evidence",
    } <= step_ids
    assert len(
        [item for item in step_ids if item.startswith("research_search_")]
    ) == 1
    assert len(
        [item for item in step_ids if item.startswith("research_call_")]
    ) == 1
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET status = 'success'
            WHERE project_id = 'project-1'
              AND operation_id = 'operation-1'
            """
        )
    with database.read_conn() as connection:
        authority = result_reader.read_success_from_connection(
            connection,
            "project-1",
            "operation-1",
            file_backend=backend,
        )
    assert authority is not None
    assert authority.result == first
    serialized_artifacts = b"\n".join(backend.files.values())
    assert b"test-only-secret" not in serialized_artifacts
    assert str(tmp_path).encode() not in serialized_artifacts


def test_paid_search_unknown_is_not_submitted_twice(
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
    settings = _settings(paid=True)
    prepared = _prepared(profile=profile, settings=settings)
    search_calls = 0

    def uncertain(*_args, **_kwargs):
        nonlocal search_calls
        search_calls += 1
        raise AppException(
            502,
            "WEB_SEARCH_UNAVAILABLE",
            "timeout after submission",
        )

    monkeypatch.setattr(web_search, "search", uncertain)
    for _attempt in range(2):
        with pytest.raises(AppException) as raised:
            execution.execute_prepared_research_evidence(
                prepared,
                execution_fence=fence,
                resolved_profile=profile,
                search_settings=settings,
                search_api_key="test-only-secret",
                is_cancelled=lambda: False,
                file_backend=backend,
                complete_json=lambda **_kwargs: pytest.fail(
                    "assessment must not run"
                ),
                clock=lambda: T0 + timedelta(seconds=1),
            )
        assert raised.value.code == (
            "VIDEO_LOCALIZATION_RESEARCH_SEARCH_RESULT_UNKNOWN"
        )
    assert search_calls == 1
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


def test_free_search_retry_uses_a_distinct_durable_attempt(
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
    monkeypatch.setattr(
        "app.domains.video_localization.web_research.time.sleep",
        lambda _seconds: None,
    )
    profile = _profile()
    settings = _settings()
    prepared = _prepared(profile=profile, settings=settings)
    search_calls = 0

    def flaky_search(*_args, **_kwargs):
        nonlocal search_calls
        search_calls += 1
        if search_calls == 1:
            raise AppException(
                502,
                "WEB_SEARCH_UNAVAILABLE",
                "temporary outage",
            )
        return [
            web_search.SearchResult(
                title="JoAnne Feeney interview profile",
                url="https://example.com/profile",
                snippet=(
                    "In this interview, JoAnne Feeney is introduced as "
                    "a portfolio manager."
                ),
            )
        ]

    def complete_json(**kwargs):
        _trace(kwargs["trace_sink"], profile)
        return {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "sufficient",
                    "reason": "Matched.",
                    "followup_query": None,
                }
            ]
        }

    monkeypatch.setattr(web_search, "search", flaky_search)
    result = execution.execute_prepared_research_evidence(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        search_settings=settings,
        search_api_key=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    assert result.status == "completed"
    assert search_calls == 2
    search_steps = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id.startswith("research_search_")
    ]
    primary_steps = sorted(
        (
            item
            for item in search_steps
            if "_primary_" in item.step_id
        ),
        key=lambda item: item.step_id,
    )
    assert [item.status for item in primary_steps] == [
        "failed",
        "success",
    ]
    assert primary_steps[0].step_id.endswith("_a1")
    assert primary_steps[1].step_id.endswith("_a2")
    assert all(item.cost_class == "external_free" for item in search_steps)


def test_reader_rejects_corrupted_referenced_search_artifact(
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
    settings = _settings()
    prepared = _prepared(profile=profile, settings=settings)
    monkeypatch.setattr(
        web_search,
        "search",
        lambda *_args, **_kwargs: [
            web_search.SearchResult(
                title="JoAnne Feeney interview profile",
                url="https://example.com/profile",
                snippet=(
                    "In this interview, JoAnne Feeney is introduced as "
                    "a portfolio manager."
                ),
            )
        ],
    )

    def complete_json(**kwargs):
        _trace(kwargs["trace_sink"], profile)
        return {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "sufficient",
                    "reason": "Matched.",
                    "followup_query": None,
                }
            ]
        }

    execution.execute_prepared_research_evidence(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        search_settings=settings,
        search_api_key=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    search_step = next(
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if (
            item.step_id.startswith("research_search_")
            and "_primary_" in item.step_id
            and item.status == "success"
        )
    )
    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        search_step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    backend.files[("project-1", artifact.storage_key)] += b"corrupt"
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET status = 'success'
            WHERE project_id = 'project-1'
              AND operation_id = 'operation-1'
            """
        )
    with database.read_conn() as connection:
        with pytest.raises(OperationDetailRepairRequired) as raised:
            result_reader.read_success_from_connection(
                connection,
                "project-1",
                "operation-1",
                file_backend=backend,
            )
    assert "artifact_invalid" in raised.value.issue_codes
