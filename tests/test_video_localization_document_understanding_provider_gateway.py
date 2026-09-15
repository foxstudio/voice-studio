from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.domains.video_localization import asr_flow  # noqa: E402
from app.domains.video_localization import (  # noqa: E402
    asr_document_understanding_provider_gateway as provider_gateway,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_document_understanding_step import (  # noqa: E402
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    parse_asr_document_understanding_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (  # noqa: E402
    ArtifactStorageKeys,
    ManagedArtifactStorageIntegrityError,
)
from app.services import database, llm_runtime  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=30)


class FakeArtifactBackend:
    ARTIFACT_STORAGE_BACKEND = "project_package"

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}

    def build_storage_keys(
        self,
        *,
        operation_id: str,
        step_attempt_id: str,
        artifact_kind: str,
        artifact_key: str,
        artifact_id: str,
        media_type: str,
    ) -> ArtifactStorageKeys:
        del media_type
        prefix = (
            f"artifacts/{operation_id}/{step_attempt_id}/"
            f"{artifact_kind}/{artifact_key}-{artifact_id}"
        )
        return ArtifactStorageKeys(
            storage_key=f"{prefix}.json",
            staging_key=f".staging/{artifact_id}.part",
        )

    def write_staging_file(
        self,
        project_id: str,
        staging_key: str,
        content: bytes,
    ) -> None:
        self.files[(project_id, staging_key)] = content

    def commit_staging_file(
        self,
        project_id: str,
        *,
        staging_key: str,
        storage_key: str,
        expected_size: int,
        expected_fingerprint: str,
    ) -> None:
        content = self.files.pop((project_id, staging_key))
        self._verify(
            content,
            expected_size,
            expected_fingerprint,
        )
        self.files[(project_id, storage_key)] = content

    def read_verified_file(
        self,
        project_id: str,
        storage_key: str,
        *,
        expected_size: int,
        expected_fingerprint: str,
    ) -> bytes:
        content = self.files[(project_id, storage_key)]
        self._verify(
            content,
            expected_size,
            expected_fingerprint,
        )
        return content

    def remove_staging_file(
        self,
        project_id: str,
        staging_key: str,
    ) -> None:
        self.files.pop((project_id, staging_key), None)

    @staticmethod
    def content_fingerprint(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _verify(
        content: bytes,
        expected_size: int,
        expected_fingerprint: str,
    ) -> None:
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest()
            != expected_fingerprint
        ):
            raise ManagedArtifactStorageIntegrityError(
                "artifact content mismatch"
            )


def _insert_operation() -> None:
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
                ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
                T0.isoformat(),
                T0.isoformat(),
            ),
        )


def _claim(
    tmp_path: Path,
    *,
    runner_id: str = "runner-1",
    observed_at: datetime = T0,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    if not attempt_store.list_attempts(
        "project-1",
        "operation-1",
    ):
        _insert_operation()
    decision = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id=runner_id,
        observed_at=observed_at,
        lease_duration=LEASE,
    )
    assert decision.acquired is True
    assert decision.attempt.execution_fence is not None
    return decision.attempt.execution_fence


def _profile(
    *,
    base_url: str = "https://llm.example.com/v1",
) -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="review-profile",
        protocol="openai_compatible",
        base_url=base_url,
        model_id="review-model",
        api_key=(
            None
            if base_url.startswith("http://127.0.0.1")
            else "top-secret-key"
        ),
    )


def _request(
    *,
    attempt: int = 1,
    trace_sink=None,
) -> asr_flow.AsrDocumentUnderstandingCompletionRequest:
    return asr_flow.AsrDocumentUnderstandingCompletionRequest(
        contract_version=(
            "asr-document-understanding-call-input-v1"
        ),
        behavior_version=asr_flow.PROMPT_VERSION,
        call_id="understand_document:full_document",
        attempt=attempt,
        system_prompt="Read the complete private transcript.",
        user_payload={
            "scene_context": "locked context",
            "document": [{"text": "Hello."}],
        },
        profile_id="review-profile",
        max_tokens=8_000,
        timeout=240,
        disable_reasoning=attempt == 2,
        trace_sink=trace_sink,
    )


def _trace(
    sink,
    *,
    profile: llm_runtime.ResolvedProfile,
    error_code: str | None = None,
    response_id: str | None = "response-1",
) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host="llm.example.com",
            request_chars=100,
            request_body_bytes=120,
            max_tokens=8_000,
            timeout_seconds=240,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason=(
                None if error_code else "stop"
            ),
            content_chars=50 if error_code is None else 0,
            reasoning_chars=0,
            response_id=response_id,
            error_code=error_code,
        )
    )


def _gateway(
    fence,
    *,
    backend: FakeArtifactBackend,
    profile: llm_runtime.ResolvedProfile,
    complete_json,
) -> provider_gateway.ManagedDocumentUnderstandingGateway:
    return provider_gateway.ManagedDocumentUnderstandingGateway(
        execution_fence=fence,
        resolved_profile=profile,
        expected_profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
        ),
        behavior_fingerprint="b" * 64,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )


def test_remote_success_is_durable_private_and_reused(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _profile()
    provider_calls: list[dict] = []
    replay_traces: list[llm_runtime.LlmCompletionTrace] = []

    def complete_json(**kwargs):
        provider_calls.append(kwargs)
        assert step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )[-1].status == "submitted"
        _trace(kwargs["trace_sink"], profile=profile)
        return {"summary": "已理解全文。"}

    gateway = _gateway(
        fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    first = gateway(_request())
    repeated = gateway(_request(trace_sink=replay_traces.append))

    assert first == repeated == {"summary": "已理解全文。"}
    assert len(provider_calls) == 1
    assert len(replay_traces) == 1
    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "success"
    assert step.cost_class == "external_paid"
    assert step.provider_idempotency_key == (
        provider_calls[0]["idempotency_key"]
    )
    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    raw = backend.files[
        ("project-1", artifact.storage_key)
    ]
    parsed = parse_asr_document_understanding_call_artifact(
        raw
    )
    assert parsed.response == {"summary": "已理解全文。"}
    assert b"top-secret-key" not in raw
    assert b"Read the complete private transcript" not in raw
    assert b"Hello." not in raw


def test_known_json_failure_uses_separate_durable_second_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _profile()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: profile,
    )

    def complete_json(**kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            _trace(
                kwargs["trace_sink"],
                profile=profile,
                error_code="llm_json_invalid",
                response_id="response-invalid",
            )
            raise llm_runtime.LlmRuntimeError(
                "invalid JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        _trace(
            kwargs["trace_sink"],
            profile=profile,
            response_id="response-valid",
        )
        return {
            "summary": "创作者讲解视频制作流程。",
            "logic": ["先介绍目标", "再说明步骤"],
            "speaker_style": "第一人称教程讲解。",
            "entities": [],
            "search_queries": [],
            "visual_questions": [],
            "sections": [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": 1,
                    "role": "完整教程",
                    "focus": ["核对步骤"],
                }
            ],
        }

    result = asr_flow.understand_document(
        [
            asr_flow.VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1_000,
                raw_text="Hello.",
            )
        ],
        language="en",
        scene_context="",
        profile_id="review-profile",
        is_cancelled=None,
        completion_gateway=_gateway(
            fence,
            backend=backend,
            profile=profile,
            complete_json=complete_json,
        ),
    )

    assert result.retry_count == 1
    assert len(provider_calls) == 2
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert [step.status for step in steps] == [
        "failed",
        "success",
    ]
    assert [step.step_id for step in steps] == [
        (
            "document_call_understand_document_"
            "full_document_attempt_1"
        ),
        (
            "document_call_understand_document_"
            "full_document_attempt_2"
        ),
    ]
    assert (
        provider_calls[0]["idempotency_key"]
        != provider_calls[1]["idempotency_key"]
    )


def test_remote_timeout_is_unknown_and_replay_is_blocked(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _profile()
    provider_calls = 0

    def complete_json(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        _trace(
            kwargs["trace_sink"],
            profile=profile,
            error_code="llm_timeout",
            response_id="response-unknown",
        )
        raise llm_runtime.LlmRuntimeError(
            "timeout",
            code="llm_timeout",
            status_code=504,
        )

    gateway = _gateway(
        fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    with pytest.raises(AppException) as first:
        gateway(_request())
    with pytest.raises(AppException) as repeated:
        gateway(_request())

    assert first.value.code == (
        "VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_RESULT_UNKNOWN"
    )
    assert repeated.value.code == first.value.code
    assert provider_calls == 1
    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "result_unknown"
    assert step.provider_request_id == "response-unknown"


def test_profile_change_is_rejected_before_provider(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    profile = _profile()

    with pytest.raises(AppException) as raised:
        provider_gateway.ManagedDocumentUnderstandingGateway(
            execution_fence=fence,
            resolved_profile=profile,
            expected_profile_configuration_fingerprint=(
                "f" * 64
            ),
            behavior_fingerprint="b" * 64,
            file_backend=FakeArtifactBackend(),
            complete_json=lambda **_kwargs: {},
        )

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROFILE_CHANGED"
    )


def test_local_unknown_result_can_replay_in_a_new_worker_attempt(
    tmp_path: Path,
) -> None:
    first_fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _profile(
        base_url="http://127.0.0.1:11434/v1"
    )
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        _trace(
            kwargs["trace_sink"],
            profile=profile,
            error_code=(
                "llm_timeout" if calls == 1 else None
            ),
            response_id=f"local-response-{calls}",
        )
        if calls == 1:
            raise llm_runtime.LlmRuntimeError(
                "timeout",
                code="llm_timeout",
                status_code=504,
            )
        return {"summary": "本地结果已恢复。"}

    first_gateway = _gateway(
        first_fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    with pytest.raises(AppException):
        first_gateway(_request())

    next_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )
    next_gateway = provider_gateway.ManagedDocumentUnderstandingGateway(
        execution_fence=next_fence,
        resolved_profile=profile,
        expected_profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
        ),
        behavior_fingerprint="b" * 64,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=32),
    )

    assert next_gateway(_request()) == {
        "summary": "本地结果已恢复。"
    }
