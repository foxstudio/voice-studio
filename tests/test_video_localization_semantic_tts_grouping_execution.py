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


from app.domains.video_localization import (  # noqa: E402
    semantic_tts_grouping_execution as execution,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_operation_artifact_storage import (  # noqa: E402
    ArtifactStorageKeys,
    ManagedArtifactStorageIntegrityError,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (  # noqa: E402
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
    parse_semantic_tts_grouping_round_artifact,
)
from app.services import database, llm_runtime  # noqa: E402
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
T0 = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
LEASE = timedelta(seconds=30)


def _steps():
    return step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )


def _step(step_id: str):
    return next(
        step for step in _steps() if step.step_id == step_id
    )


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
        staged = (project_id, staging_key)
        final = (project_id, storage_key)
        if final in self.files:
            self._verify(
                self.files[final],
                expected_size,
                expected_fingerprint,
            )
            self.files.pop(staged, None)
            return
        content = self.files.pop(staged)
        self._verify(
            content,
            expected_size,
            expected_fingerprint,
        )
        self.files[final] = content

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


def _draft() -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue-1",
                speaker_id="speaker-a",
                start_ms=0,
                end_ms=1_000,
            ),
            VideoLocalizationCue(
                cue_id="cue-2",
                speaker_id="speaker-a",
                start_ms=1_000,
                end_ms=2_000,
            ),
            VideoLocalizationCue(
                cue_id="cue-3",
                speaker_id="speaker-b",
                start_ms=2_000,
                end_ms=3_000,
            ),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-1",
                start_ms=0,
                end_ms=1_000,
                text="先介绍事情的背景。",
                source_cue_ids=["cue-1"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-2",
                start_ms=1_000,
                end_ms=2_000,
                text="接着说明具体做法。",
                source_cue_ids=["cue-2"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-3",
                start_ms=2_000,
                end_ms=3_000,
                text="另一个人作出回应。",
                source_cue_ids=["cue-3"],
            ),
        ],
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
                'semantic_tts_grouping',
                'running',
                'parameters-v1',
                ?,
                'command',
                ?,
                ?
            )
            """,
            (
                SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
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


def _remote_profile() -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="work",
        protocol="openai_compatible",
        base_url="https://llm.example.com/v1",
        model_id="model-a",
        api_key="secret",
    )


def _local_profile() -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="local",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:11434/v1",
        model_id="local-model",
    )


def _codex_profile() -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="codex",
        protocol="codex_cli",
        base_url="",
        model_id="gpt-codex",
        provider_model_id="gpt-codex",
    )


def _trace(
    sink,
    *,
    round_index: int,
    profile: llm_runtime.ResolvedProfile,
    response_id: str | None,
) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host=(
                "127.0.0.1"
                if profile.base_url.startswith("http://127.0.0.1")
                else "llm.example.com"
            ),
            request_chars=100,
            request_body_bytes=120,
            max_tokens=1200,
            timeout_seconds=180,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason="stop",
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
            content_chars=50,
            reasoning_chars=0,
            response_id=response_id,
        )
    )


def test_remote_success_is_paid_durable_and_reuses_without_provider(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()
    calls: list[dict] = []

    def complete_json(**kwargs):
        current = step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        assert current[-1].status == "submitted"
        assert (
            current[-1].provider_idempotency_key
            == kwargs["idempotency_key"]
        )
        calls.append(kwargs)
        assert kwargs["resolved_profile"] is profile
        _trace(
            kwargs["trace_sink"],
            round_index=1,
            profile=profile,
            response_id="response-1",
        )
        return {
            "groups": [
                ["localized-1", "localized-2"],
                ["localized-3"],
            ]
        }

    first = execution.execute_semantic_tts_grouping(
        fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
        target_chars=60,
        max_chars=80,
    )
    repeated = execution.execute_semantic_tts_grouping(
        fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=lambda **_kwargs: pytest.fail(
            "Provider must not be called for a committed step"
        ),
        clock=lambda: T0 + timedelta(seconds=2),
        target_chars=60,
        max_chars=80,
    )

    assert first == repeated
    assert [
        group["subtitle_ids"] for group in first["groups"]
    ] == [
        ["localized-1", "localized-2"],
        ["localized-3"],
    ]
    assert len(calls) == 1
    assert calls[0]["idempotency_key"].startswith("vsl_semgrp_")
    step = _step("semantic_grouping_round_1")
    assert step.status == "success"
    assert step.cost_class == "external_paid"
    assert step.provider_name == "openai_compatible_remote"
    assert step.provider_request_id == "response-1"
    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    parsed = parse_semantic_tts_grouping_round_artifact(
        backend.read_verified_file(
            "project-1",
            artifact.storage_key,
            expected_size=artifact.size_bytes,
            expected_fingerprint=artifact.content_fingerprint,
        )
    )
    assert parsed.groups == [
        ["localized-1", "localized-2"],
        ["localized-3"],
    ]


def test_local_openai_profile_is_external_free(tmp_path: Path):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _local_profile()

    def complete_json(**kwargs):
        _trace(
            kwargs["trace_sink"],
            round_index=1,
            profile=profile,
            response_id="local-response",
        )
        return {
            "groups": [
                ["localized-1", "localized-2"],
                ["localized-3"],
            ]
        }

    execution.execute_semantic_tts_grouping(
        fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )

    step = _step("semantic_grouping_round_1")
    assert step.cost_class == "external_free"
    assert step.provider_name == "openai_compatible_local"


def test_codex_profile_is_external_paid_without_endpoint_leak():
    classified = execution.classify_provider_profile(
        _codex_profile()
    )

    assert classified.cost_class == "external_paid"
    assert classified.provider_name == "codex_cli"
    assert len(classified.endpoint_fingerprint) == 64
    assert "codex" not in classified.endpoint_fingerprint


def test_profile_configuration_fingerprint_covers_execution_identity():
    original = _remote_profile()
    first = execution.provider_configuration_fingerprint(original)

    assert first == execution.provider_configuration_fingerprint(
        original
    )
    assert first != execution.provider_configuration_fingerprint(
        llm_runtime.ResolvedProfile(
            **{
                **original.__dict__,
                "model_id": "model-b",
            }
        )
    )
    assert first != execution.provider_configuration_fingerprint(
        llm_runtime.ResolvedProfile(
            **{
                **original.__dict__,
                "base_url": "https://other.example.com/v1",
            }
        )
    )
    assert first != execution.provider_configuration_fingerprint(
        llm_runtime.ResolvedProfile(
            **{
                **original.__dict__,
                "reasoning_effort": "high",
            }
        )
    )


def test_changed_profile_is_rejected_before_step_or_provider(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()
    calls = 0

    def complete_json(**_kwargs):
        nonlocal calls
        calls += 1
        pytest.fail("changed profile must fail before Provider")

    with pytest.raises(AppException) as exc_info:
        execution.execute_semantic_tts_grouping(
            fence,
            _draft(),
            resolved_profile=profile,
            expected_profile_configuration_fingerprint=("0" * 64),
            file_backend=backend,
            complete_json=complete_json,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    assert exc_info.value.code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED"
    )
    assert calls == 0
    assert [
        (
            step.step_id,
            step.status,
            step.cost_class,
            step.error_code,
        )
        for step in _steps()
    ] == [
        (
            "prepare",
            "failed",
            "local_free",
            (
                "VIDEO_LOCALIZATION_"
                "SEMANTIC_GROUPING_PROFILE_CHANGED"
            ),
        )
    ]
    with database.conn() as connection:
        artifact_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_artifacts
            WHERE project_id = ? AND operation_id = ?
            """,
            ("project-1", "operation-1"),
        ).fetchone()[0]
    assert artifact_count == 0


def test_invalid_first_round_is_audited_before_second_round(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()
    calls: list[dict] = []
    responses = iter(
        [
            {
                "groups": [
                    ["localized-1", "localized-3"],
                    ["localized-2"],
                ]
            },
            {
                "groups": [
                    ["localized-1", "localized-2"],
                    ["localized-3"],
                ]
            },
        ]
    )

    def complete_json(**kwargs):
        calls.append(kwargs)
        round_index = len(calls)
        _trace(
            kwargs["trace_sink"],
            round_index=round_index,
            profile=profile,
            response_id=f"response-{round_index}",
        )
        return next(responses)

    result = execution.execute_semantic_tts_grouping(
        fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
        target_chars=60,
        max_chars=80,
    )

    assert len(calls) == 2
    assert "previous_error" not in calls[0]["user_payload"]
    assert "连续" in calls[1]["user_payload"]["previous_error"]
    assert len(result["llm_calls"]) == 2
    assert [
        step.status
        for step in _steps()
        if step.step_id.startswith("semantic_grouping_round_")
    ] == ["success", "success"]
    assert {
        step.step_id: step.status
        for step in _steps()
        if not step.step_id.startswith("semantic_grouping_round_")
    } == {
        "prepare": "success",
        "group": "success",
        "validate": "success",
    }


def test_new_worker_reuses_completed_round_after_crash_before_draft_commit(
    tmp_path: Path,
):
    first_fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()

    def complete_json(**kwargs):
        _trace(
            kwargs["trace_sink"],
            round_index=1,
            profile=profile,
            response_id="response-1",
        )
        return {
            "groups": [
                ["localized-1", "localized-2"],
                ["localized-3"],
            ]
        }

    first = execution.execute_semantic_tts_grouping(
        first_fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    second_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + LEASE + timedelta(seconds=1),
    )
    repeated = execution.execute_semantic_tts_grouping(
        second_fence,
        _draft(),
        resolved_profile=profile,
        file_backend=backend,
        complete_json=lambda **_kwargs: pytest.fail(
            "Provider must not be called after durable success"
        ),
        clock=lambda: T0 + LEASE + timedelta(seconds=2),
    )

    assert repeated == first
    assert {
        step.step_id: step.status for step in _steps()
    } == {
        "prepare": "success",
        "group": "success",
        "semantic_grouping_round_1": "success",
        "validate": "success",
    }


def test_remote_timeout_becomes_unknown_and_same_attempt_cannot_replay(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()
    calls = 0

    def timeout(**kwargs):
        nonlocal calls
        calls += 1
        raise llm_runtime.LlmRuntimeError(
            "secret provider detail",
            code="llm_timeout",
            status_code=504,
        )

    with pytest.raises(AppException) as first_error:
        execution.execute_semantic_tts_grouping(
            fence,
            _draft(),
            resolved_profile=profile,
            file_backend=backend,
            complete_json=timeout,
            clock=lambda: T0 + timedelta(seconds=1),
        )
    with pytest.raises(AppException) as repeated_error:
        execution.execute_semantic_tts_grouping(
            fence,
            _draft(),
            resolved_profile=profile,
            file_backend=backend,
            complete_json=timeout,
            clock=lambda: T0 + timedelta(seconds=2),
        )

    assert first_error.value.code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN"
    )
    assert repeated_error.value.code == first_error.value.code
    assert "secret provider detail" not in first_error.value.message
    assert calls == 1
    step = _step("semantic_grouping_round_1")
    assert step.status == "result_unknown"
    assert step.error_code == (
        "VIDEO_LOCALIZATION_LLM_RESULT_UNKNOWN"
    )
    group_step = _step("group")
    assert group_step.status == "failed"
    assert group_step.error_code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN"
    )


def test_two_invalid_rounds_fail_after_preserving_both_artifacts(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()
    calls = 0

    def invalid(**kwargs):
        nonlocal calls
        calls += 1
        _trace(
            kwargs["trace_sink"],
            round_index=calls,
            profile=profile,
            response_id=f"response-{calls}",
        )
        return {"groups": [["localized-1", "localized-3"]]}

    with pytest.raises(AppException) as exc_info:
        execution.execute_semantic_tts_grouping(
            fence,
            _draft(),
            resolved_profile=profile,
            file_backend=backend,
            complete_json=invalid,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    assert exc_info.value.code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID"
    )
    assert calls == 2
    assert [
        step.status
        for step in _steps()
        if step.step_id.startswith("semantic_grouping_round_")
    ] == ["success", "success"]
    assert {
        step.step_id: (
            step.status,
            step.error_code,
        )
        for step in _steps()
        if not step.step_id.startswith("semantic_grouping_round_")
    } == {
        "prepare": ("success", None),
        "group": ("success", None),
        "validate": (
            "failed",
            "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID",
        ),
    }


def test_known_provider_rejection_is_failed_without_error_text_leak(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    profile = _remote_profile()

    def reject(**_kwargs):
        raise llm_runtime.LlmRuntimeError(
            "secret authentication response",
            code="llm_auth_failed",
            status_code=401,
        )

    with pytest.raises(AppException) as exc_info:
        execution.execute_semantic_tts_grouping(
            fence,
            _draft(),
            resolved_profile=profile,
            file_backend=backend,
            complete_json=reject,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    assert exc_info.value.code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROVIDER_FAILED"
    )
    assert "secret authentication response" not in exc_info.value.message
    step = _step("semantic_grouping_round_1")
    assert step.status == "failed"
    assert step.error_code == (
        "VIDEO_LOCALIZATION_LLM_AUTH_FAILED"
    )
    group_step = _step("group")
    assert group_step.status == "failed"
    assert group_step.error_code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROVIDER_FAILED"
    )
