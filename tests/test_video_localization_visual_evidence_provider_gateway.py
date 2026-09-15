from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domains.video_localization import (
    asr_visual_evidence_managed_contracts as managed_contracts,
    asr_visual_evidence_provider_gateway as provider_gateway,
    managed_artifact_files,
    visual_evidence,
)
from app.errors import AppException
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.voice_studio import AppSettings
from app.services import database, llm_runtime, settings_store
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


T0 = datetime.now(timezone.utc)
JPEG = b"\xff\xd8\xff\xe0managed-visual-frame\xff\xd9"


def _claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            voice_dir=str(tmp_path / "voices"),
            log_dir=str(tmp_path / "logs"),
        )
    )
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
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
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


def _profile() -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="vision-profile",
        protocol="openai_compatible",
        base_url="https://vision.example.com/v1",
        model_id="vision-model",
        api_key="secret",
    )


def _frame_reference():
    import hashlib

    digest = hashlib.sha256(JPEG).hexdigest()
    return managed_contracts.AsrVisualEvidenceFrameReferenceV1(
        frame_id="frame-a",
        question_id="visual_01",
        frame_index=1,
        round_index=1,
        timestamp_ms=1_000,
        sha256=digest,
        size_bytes=len(JPEG),
        artifact_id="artifact-frame-a",
        artifact_fingerprint=digest,
    )


def _request(*, trace_sink=None):
    frame = visual_evidence.AsrVisualEvidenceFrame(
        frame_id="frame-a",
        question_id="visual_01",
        frame_index=1,
        round_index=1,
        timestamp_ms=1_000,
        file_name="ephemeral.jpg",
        sha256=_frame_reference().sha256,
        size_bytes=len(JPEG),
    )
    return visual_evidence.VisualEvidenceCompletionRequest(
        call_id="visual:visual_01:r1",
        question_id="visual_01",
        round_index=1,
        system_prompt="Inspect the private frame.",
        user_payload={"question": "private title"},
        frames=(frame,),
        images=(
            llm_runtime.LlmImageInput(
                data=JPEG,
                media_type="image/jpeg",
            ),
        ),
        profile_id="vision-profile",
        max_tokens=1_600,
        timeout=120,
        disable_reasoning=True,
        trace_sink=trace_sink,
    )


def _trace(sink, *, error_code: str | None = None) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id="vision-profile",
            model_id="vision-model",
            provider_host="vision.example.com",
            request_chars=100,
            request_body_bytes=150,
            max_tokens=1_600,
            timeout_seconds=120,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=20,
            finish_reason=None if error_code else "stop",
            content_chars=0 if error_code else 40,
            reasoning_chars=0,
            response_id="response-1",
            error_code=error_code,
        )
    )


def _gateway(fence, complete):
    profile = _profile()
    reference = _frame_reference()
    return provider_gateway.ManagedVisualEvidenceGateway(
        execution_fence=fence,
        resolved_profile=profile,
        expected_profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
        ),
        behavior_fingerprint="b" * 64,
        frame_reference=lambda frame: (
            reference
            if frame.frame_id == reference.frame_id
            else (_ for _ in ()).throw(KeyError(frame.frame_id))
        ),
        complete_multimodal_json=complete,
        clock=lambda: T0 + timedelta(seconds=1),
    )


def test_remote_visual_success_is_idempotent_and_private(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    calls: list[dict] = []
    replay_traces: list[llm_runtime.LlmCompletionTrace] = []

    def complete(**kwargs):
        calls.append(kwargs)
        _trace(kwargs["trace_sink"])
        return {
            "answer": "Visible title",
            "relevant_frame_indexes": [1],
        }

    gateway = _gateway(fence, complete)
    first = gateway(_request())
    replay = gateway(_request(trace_sink=replay_traces.append))

    assert first == replay
    assert len(calls) == 1
    assert len(replay_traces) == 1
    assert calls[0]["resolved_profile"] == _profile()
    assert calls[0]["idempotency_key"].startswith("vsl_asr_vis_")
    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "success"
    assert step.cost_class == "external_paid"
    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    raw = artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=managed_artifact_files,
    ).content
    assert b"secret" not in raw
    assert b"Inspect the private frame" not in raw
    assert b"private title" not in raw
    assert JPEG not in raw


def test_uncertain_visual_failure_blocks_automatic_replay(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    calls = 0

    def complete(**kwargs):
        nonlocal calls
        calls += 1
        _trace(kwargs["trace_sink"], error_code="llm_timeout")
        raise llm_runtime.LlmRuntimeError(
            "timeout",
            code="llm_timeout",
            status_code=504,
        )

    gateway = _gateway(fence, complete)
    for _ in range(2):
        with pytest.raises(AppException) as caught:
            gateway(_request())
        assert caught.value.code == (
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_RESULT_UNKNOWN"
        )

    assert calls == 1
    assert step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0].status == "result_unknown"
