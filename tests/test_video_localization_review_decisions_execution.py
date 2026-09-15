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
    asr_review_decisions_execution as execution,
    asr_review_decisions_managed_contracts as managed_contracts,
    asr_review_decisions_result_reader as result_reader,
    asr_section_review_result_reader,
    asr_section_review_execution,
    asr_section_review_operation_projection,
    asr_targeted_relisten,
    managed_local_step,
    media_assets,
    operation_detail_reader,
    operation_queue,
    review_decisions,
    section_review,
    service,
    source_pipeline,
)
from app.domains.video_localization.operation_detail_errors import (  # noqa: E402
    OperationDetailRepairRequired,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_review_decisions_step import (  # noqa: E402
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_section_review_step import (  # noqa: E402
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    ProjectCreate,
)
from app.schemas.video_localization_operation_detail import (  # noqa: E402
    AsrReviewDecisionsDetailParametersV1,
    OperationDetailCoreV1,
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
    video_localization_operation_detail_core_store as detail_store,
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
                ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
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


def _request(
    *,
    issue_count: int = 2,
) -> review_decisions.AsrReviewDecisionsInput:
    segments = [
        {
            "segment_id": "asr_0001",
            "start_ms": 0,
            "end_ms": 1_000,
            "raw_text": "This is teh first line.",
        },
        {
            "segment_id": "asr_0002",
            "start_ms": 1_000,
            "end_ms": 2_000,
            "raw_text": "We recieve the result.",
        },
    ]
    issues = [
        {
            "issue_id": "issue-1",
            "section_id": "opening",
            "segment_id": "asr_0001",
            "current_excerpt": "teh",
            "proposed_replacement": "the",
            "reason": "拼写疑点",
            "confidence": 0.9,
        },
        {
            "issue_id": "issue-2",
            "section_id": "opening",
            "segment_id": "asr_0002",
            "current_excerpt": "recieve",
            "proposed_replacement": "receive",
            "reason": "拼写疑点",
            "confidence": 0.9,
        },
    ][:issue_count]
    return review_decisions.AsrReviewDecisionsInput(
        upstream_operation_id="section-operation",
        source_track_id="vocals",
        source_audio_sha256="e" * 64,
        language="en",
        profile_id="review-profile",
        document_summary="Two short transcript lines.",
        segments=segments,
        issues=issues,
        upstream_status="completed",
    )


def _prepared(
    profile: llm_runtime.ResolvedProfile,
    *,
    issue_count: int = 2,
) -> managed_contracts.AsrReviewDecisionsPreparedInputV1:
    return managed_contracts.AsrReviewDecisionsPreparedInputV1(
        section_review_artifact_fingerprint="a" * 64,
        profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(profile) if issue_count else None
        ),
        behavior_fingerprint=(execution.review_decisions_behavior_fingerprint()),
        request=_request(issue_count=issue_count),
    )


def _section_result(
    *,
    issue_count: int = 2,
) -> section_review.AsrSectionReviewResult:
    request = _request(issue_count=issue_count)
    return section_review.AsrSectionReviewResult(
        input=section_review.AsrSectionReviewInput(
            upstream_contract_version=("asr-entity-normalization-v1"),
            upstream_operation_id="entity-operation",
            understanding_operation_id="document-operation",
            source_track_id=request.source_track_id,
            source_audio_sha256=request.source_audio_sha256,
            language=request.language,
            profile_id=request.profile_id,
            document_summary=request.document_summary,
            sections=[
                {
                    "section_id": "opening",
                    "start_ordinal": 1,
                    "end_ordinal": 2,
                    "start_segment_id": "asr_0001",
                    "end_segment_id": "asr_0002",
                    "role": "opening",
                }
            ],
            segments=request.segments,
        ),
        status="completed",
        profile_id=request.profile_id,
        model_id="review-model",
        section_runs=[],
        issues=[item.model_dump(mode="json") for item in request.issues],
        duration_ms=20,
        quality_summary={
            "status": "passed",
            "section_count": 1,
            "checked_section_count": 1,
            "issue_count": issue_count,
            "sections_cover_all_segments": True,
            "source_text_unchanged": True,
        },
    )


def _trace(sink, profile, response_id: str) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host="127.0.0.1",
            request_chars=100,
            request_body_bytes=200,
            max_tokens=8_000,
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


def _decision(issue_id: str) -> dict:
    replacement = "the" if issue_id == "issue-1" else "receive"
    return {
        "issue_id": issue_id,
        "accept": True,
        "replacement": replacement,
        "reason": "结合上下文应采用建议拼写。",
        "confidence": 0.95,
        "needs_confirmation": False,
        "evidence_source_ids": [],
    }


def test_managed_review_decisions_replays_primary_and_coverage(
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
    submitted: list[tuple[str, ...]] = []

    def complete_json(**kwargs):
        issue_ids = tuple(item["issue_id"] for item in kwargs["user_payload"]["candidate_issues"])
        submitted.append(issue_ids)
        _trace(
            kwargs["trace_sink"],
            profile,
            "-".join(issue_ids),
        )
        returned = ("issue-1",) if issue_ids == ("issue-1", "issue-2") else issue_ids
        return {"decisions": [_decision(issue_id) for issue_id in returned]}

    first = execution.execute_prepared_review_decisions(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    replay = execution.execute_prepared_review_decisions(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert replay == first
    assert submitted == [
        ("issue-1", "issue-2"),
        ("issue-2",),
    ]
    assert len(first.llm_calls) == 2
    assert [item.call_id for item in first.llm_calls] == [
        "review-decisions-r1-primary-a01",
        "review-decisions-r1-coverage-a01",
    ]
    assert [item.corrected_text for item in first.updated_segments] == [
        "This is the first line.",
        "We receive the result.",
    ]
    step_ids = {
        item.step_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    }
    assert {
        "prepare_review_decisions_input",
        "decision_call_primary_attempt_1",
        "decision_call_coverage_attempt_1",
        "finalize_review_decisions",
    } <= step_ids


def test_zero_issue_review_decisions_uses_no_profile_or_provider(
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
    called = False

    def complete_json(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("zero-issue result must stay local")

    result = execution.execute_prepared_review_decisions(
        _prepared(profile, issue_count=0),
        execution_fence=fence,
        resolved_profile=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )

    assert called is False
    assert result.model_id is None
    assert result.llm_calls == []
    assert result.status == "completed"


def test_primary_and_coverage_keep_independent_retry_sequences(
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
    calls_by_issues: dict[tuple[str, ...], int] = {}

    def complete_json(**kwargs):
        issue_ids = tuple(item["issue_id"] for item in kwargs["user_payload"]["candidate_issues"])
        calls_by_issues[issue_ids] = calls_by_issues.get(issue_ids, 0) + 1
        if calls_by_issues[issue_ids] == 1:
            raise llm_runtime.LlmRuntimeError(
                "invalid JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        _trace(
            kwargs["trace_sink"],
            profile,
            f"retry-{len(calls_by_issues)}-{issue_ids[-1]}",
        )
        returned = ("issue-1",) if issue_ids == ("issue-1", "issue-2") else issue_ids
        return {"decisions": [_decision(issue_id) for issue_id in returned]}

    result = execution.execute_prepared_review_decisions(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )

    assert calls_by_issues == {
        ("issue-1", "issue-2"): 2,
        ("issue-2",): 2,
    }
    assert [item.call_id for item in result.llm_calls] == [
        "review-decisions-r1-primary-a02",
        "review-decisions-r1-coverage-a02",
    ]
    known_steps = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id.startswith("decision_call_")
    ]
    assert sorted([(item.step_id, item.status) for item in known_steps]) == [
        ("decision_call_coverage_attempt_1", "failed"),
        ("decision_call_coverage_attempt_2", "success"),
        ("decision_call_primary_attempt_1", "failed"),
        ("decision_call_primary_attempt_2", "success"),
    ]


def test_paid_unknown_review_decision_is_not_submitted_twice(
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
    prepared = _prepared(profile, issue_count=1)
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
            execution.execute_prepared_review_decisions(
                prepared,
                execution_fence=fence,
                resolved_profile=profile,
                is_cancelled=lambda: False,
                file_backend=backend,
                complete_json=uncertain,
            )
        assert raised.value.code == ("VIDEO_LOCALIZATION_REVIEW_DECISIONS_RESULT_UNKNOWN")
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


def test_review_decisions_reader_uses_locked_same_snapshot(
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
    section_result = _section_result(issue_count=1)
    request = review_decisions.build_review_decisions_input(
        section_result,
        upstream_operation_id="section-operation",
    )
    request = request.model_copy(
        update={
            "acoustic_candidates": [
                asr_targeted_relisten.AsrAcousticCandidate(
                    section_id=request.issues[0].section_id,
                    issue_ids=[request.issues[0].issue_id],
                    start_ms=request.segments[0].start_ms,
                    end_ms=request.segments[0].end_ms,
                    text="AI means less demand.",
                )
            ]
        }
    )
    profile_fingerprint = provider_execution.provider_configuration_fingerprint(profile)
    prepared = managed_contracts.AsrReviewDecisionsPreparedInputV1(
        section_review_artifact_fingerprint="a" * 64,
        profile_configuration_fingerprint=profile_fingerprint,
        behavior_fingerprint=(execution.review_decisions_behavior_fingerprint()),
        request=request,
    )

    def complete_json(**kwargs):
        _trace(kwargs["trace_sink"], profile, "primary")
        return {"decisions": [_decision("issue-1")]}

    expected = execution.execute_prepared_review_decisions(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )
    detail_store.put_detail_core(
        OperationDetailCoreV1(
            project_id="project-1",
            operation_id="operation-1",
            kind="english_asr",
            workflow_version=(ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION),
            parameters=AsrReviewDecisionsDetailParametersV1(
                input_section_review_operation_id=("section-operation"),
                section_review_artifact_fingerprint="a" * 64,
                profile_id=profile.profile_id,
                profile_configuration_fingerprint=(profile_fingerprint),
                behavior_fingerprint=(execution.review_decisions_behavior_fingerprint()),
            ),
        ),
        written_at=T0.isoformat(),
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET status = 'success', completed_at = ?, updated_at = ?
            WHERE project_id = 'project-1'
              AND operation_id = 'operation-1'
            """,
            (T0.isoformat(), T0.isoformat()),
        )
    monkeypatch.setattr(
        asr_section_review_result_reader,
        "read_result_from_connection",
        lambda *_args, **_kwargs: asr_section_review_result_reader.ManagedSectionReviewAuthority(
            result=section_result,
            final_artifact_fingerprint="a" * 64,
        ),
    )

    with database.read_conn() as connection:
        authority = result_reader.read_result_from_connection(
            connection,
            "project-1",
            "operation-1",
            file_backend=backend,
        )

    assert authority is not None
    assert authority.result == expected
    assert len(authority.final_artifact_fingerprint) == 64

    final_step = next(
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id == "finalize_review_decisions"
    )
    with database.read_conn() as connection:
        final_artifact = artifact_store.get_step_artifact_from_connection(
            connection,
            "project-1",
            "operation-1",
            final_step.step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
    assert final_artifact is not None
    backend.files[("project-1", final_artifact.storage_key)] = b"{}"
    with database.read_conn() as connection:
        with pytest.raises(OperationDetailRepairRequired):
            result_reader.read_result_from_connection(
                connection,
                "project-1",
                "operation-1",
                file_backend=backend,
            )
