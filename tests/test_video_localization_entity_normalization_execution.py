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
    asr_entity_normalization_execution as execution,
    asr_entity_normalization_managed_contracts as managed_contracts,
    entity_normalization,
    managed_local_step,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_entity_normalization_step import (  # noqa: E402
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
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
                ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
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
    profile_id: str | None,
    glossary: list[dict] | None = None,
) -> entity_normalization.AsrEntityNormalizationInput:
    return entity_normalization.AsrEntityNormalizationInput(
        upstream_contract_version="asr-research-evidence-v1",
        upstream_operation_id="research-operation",
        source_track_id="vocals",
        source_audio_sha256="e" * 64,
        language="en",
        profile_id=profile_id,
        document_summary="The guest discusses AI chips.",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2_000,
                "text": "Joan Feeney discusses AI chips.",
            }
        ],
        candidates=[
            {
                "candidate_id": "candidate_01",
                "query": "JoAnne Feeney AI chips interview",
                "category": "proper_noun",
                "reason": "Verify the guest name.",
                "target_terms": ["Joan Feeney"],
            }
        ],
        evidence=[
            {
                "evidence_id": "evidence_01",
                "candidate_id": "candidate_01",
                "query_run_id": "query_01",
                "provider": "wikipedia",
                "title": "JoAnne Feeney discusses AI chips",
                "url": "https://example.com/interview",
                "snippet": (
                    "Portfolio manager JoAnne Feeney discusses AI chips."
                ),
                "retrieved_at": T0.isoformat(),
                "matched_target_terms": ["Joan Feeney"],
            }
        ],
        glossary=glossary or [],
    )


def _prepared(
    profile: llm_runtime.ResolvedProfile | None,
    *,
    glossary: list[dict] | None = None,
) -> managed_contracts.AsrEntityNormalizationPreparedInputV1:
    request = _request(
        profile_id=profile.profile_id if profile else None,
        glossary=glossary,
    )
    return (
        managed_contracts.AsrEntityNormalizationPreparedInputV1(
            research_artifact_fingerprint="a" * 64,
            glossary_fingerprint=(
                managed_contracts.glossary_fingerprint(
                    request.glossary
                )
            ),
            profile_configuration_fingerprint=(
                provider_execution
                .provider_configuration_fingerprint(profile)
                if profile
                else None
            ),
            behavior_fingerprint=(
                execution
                .entity_normalization_behavior_fingerprint()
            ),
            request=request,
        )
    )


def _trace(sink, profile, response_id: str) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host="127.0.0.1",
            request_chars=100,
            request_body_bytes=200,
            max_tokens=3_000,
            timeout_seconds=120,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason="stop",
            content_chars=40,
            reasoning_chars=0,
            response_id=response_id,
        )
    )


def test_managed_entity_normalization_replays_calls_and_final(
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
    call_ids: list[str] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        if "candidate_entities" in payload:
            call_ids.append("entity_resolution")
            _trace(kwargs["trace_sink"], profile, "resolution-1")
            return {
                "resolutions": [
                    {
                        "canonical_name": "JoAnne Feeney",
                        "variants": ["Joan Feeney"],
                        "role": "投资经理",
                        "confidence": 0.95,
                        "evidence_source_ids": ["evidence_01"],
                    }
                ]
            }
        call_ids.append("entity_variant_mapping")
        _trace(kwargs["trace_sink"], profile, "mapping-1")
        return {
            "mappings": [
                {
                    "canonical_name": "JoAnne Feeney",
                    "variants": ["Joan Feeney"],
                    "reason": "same guest",
                }
            ]
        }

    first = execution.execute_prepared_entity_normalization(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    replay = execution.execute_prepared_entity_normalization(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert replay == first
    assert call_ids == [
        "entity_resolution",
        "entity_variant_mapping",
    ]
    assert first.updated_segments[0].corrected_text == (
        "JoAnne Feeney discusses AI chips."
    )
    assert len(first.llm_calls) == 2
    step_ids = {
        item.step_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    }
    assert {
        "prepare_entity_normalization_input",
        "entity_call_entity_resolution_attempt_1",
        "entity_call_entity_variant_mapping_attempt_1",
        "finalize_entity_normalization",
    } <= step_ids
    serialized = b"\n".join(backend.files.values())
    assert b"test-only-secret" not in serialized
    assert str(tmp_path).encode() not in serialized


def test_glossary_only_entity_normalization_uses_no_model(
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
    request = _request(
        profile_id=None,
        glossary=[
            {
                "glossary_id": "term_01",
                "source_text": "Joan Feeney",
                "corrected_source_text": "JoAnne Feeney",
                "zh_text": "乔安·菲尼",
            }
        ],
    ).model_copy(update={"candidates": [], "evidence": []})
    prepared = (
        managed_contracts.AsrEntityNormalizationPreparedInputV1(
            research_artifact_fingerprint="a" * 64,
            glossary_fingerprint=(
                managed_contracts.glossary_fingerprint(
                    request.glossary
                )
            ),
            behavior_fingerprint=(
                execution
                .entity_normalization_behavior_fingerprint()
            ),
            request=request,
        )
    )
    result = execution.execute_prepared_entity_normalization(
        prepared,
        execution_fence=fence,
        resolved_profile=None,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=lambda **_kwargs: pytest.fail(
            "glossary-only task must not call a model"
        ),
    )
    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text.startswith(
        "JoAnne Feeney"
    )
    assert result.llm_calls == []


def test_paid_unknown_entity_call_is_not_submitted_twice(
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
    prepared = _prepared(profile)
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
            execution.execute_prepared_entity_normalization(
                prepared,
                execution_fence=fence,
                resolved_profile=profile,
                is_cancelled=lambda: False,
                file_backend=backend,
                complete_json=uncertain,
            )
        assert raised.value.code == (
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_RESULT_UNKNOWN"
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


def test_retryable_resolution_output_uses_second_attempt(
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
        _trace(kwargs["trace_sink"], profile, f"response-{calls}")
        if "candidate_entities" in kwargs["user_payload"]:
            return {"resolutions": []}
        return {"mappings": []}

    result = execution.execute_prepared_entity_normalization(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )
    assert result.status == "completed"
    assert calls == 2
    replay = execution.execute_prepared_entity_normalization(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )
    assert replay == result
    assert calls == 2
    resolution_steps = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id.startswith(
            "entity_call_entity_resolution"
        )
    ]
    assert [item.status for item in resolution_steps] == [
        "failed",
        "success",
    ]
