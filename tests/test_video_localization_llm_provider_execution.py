from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.services import llm_runtime  # noqa: E402
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)  # noqa: E402
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)  # noqa: E402


def _profile(
    *,
    base_url: str = "https://provider.example/v1",
    api_key: str = "secret-a",
    model_id: str = "model-a",
    provider_model_id: str | None = None,
    reasoning_effort: str | None = "medium",
) -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="profile-a",
        protocol="openai_compatible",
        base_url=base_url,
        model_id=model_id,
        provider_model_id=provider_model_id,
        reasoning_effort=reasoning_effort,
        api_key=api_key,
    )


def _fence(
    *,
    attempt_id: str = "attempt-a",
) -> ExecutionFence:
    return ExecutionFence(
        project_id="project-a",
        operation_id="operation-a",
        attempt_id=attempt_id,
        fencing_token=3,
        runner_id="runner-a",
    )


def test_classification_separates_local_and_remote_cost() -> None:
    remote = provider_execution.classify_provider_profile(_profile())
    local = provider_execution.classify_provider_profile(_profile(base_url="http://127.0.0.1:9000/v1"))

    assert remote.provider_name == "openai_compatible_remote"
    assert remote.cost_class == "external_paid"
    assert local.provider_name == "openai_compatible_local"
    assert local.cost_class == "external_free"
    assert remote.endpoint_fingerprint != local.endpoint_fingerprint


def test_configuration_fingerprint_excludes_secret_but_locks_execution() -> None:
    original = _profile()
    fingerprint = provider_execution.provider_configuration_fingerprint(original)

    assert fingerprint == (provider_execution.provider_configuration_fingerprint(replace(original, api_key="secret-b")))
    assert fingerprint != (provider_execution.provider_configuration_fingerprint(replace(original, model_id="model-b")))
    assert fingerprint != (
        provider_execution.provider_configuration_fingerprint(replace(original, reasoning_effort="high"))
    )
    assert fingerprint != (
        provider_execution.provider_configuration_fingerprint(
            replace(
                original,
                base_url="https://other.example/v1",
            )
        )
    )


def test_provider_idempotency_key_is_stable_and_attempt_scoped() -> None:
    first = provider_execution.provider_idempotency_key(
        _fence(),
        key_prefix="vsl_test_",
        step_id="understand_document:full_document:attempt_1",
        input_fingerprint="input-a",
    )
    repeated = provider_execution.provider_idempotency_key(
        _fence(),
        key_prefix="vsl_test_",
        step_id="understand_document:full_document:attempt_1",
        input_fingerprint="input-a",
    )
    next_attempt = provider_execution.provider_idempotency_key(
        _fence(attempt_id="attempt-b"),
        key_prefix="vsl_test_",
        step_id="understand_document:full_document:attempt_1",
        input_fingerprint="input-a",
    )

    assert first == repeated
    assert first.startswith("vsl_test_")
    assert len(first) == len("vsl_test_") + 64
    assert first != next_attempt


@pytest.mark.parametrize(
    "key_prefix",
    ["", "contains space_", "x" * 33],
)
def test_provider_idempotency_key_rejects_invalid_prefix(
    key_prefix: str,
) -> None:
    with pytest.raises(ValueError):
        provider_execution.provider_idempotency_key(
            _fence(),
            key_prefix=key_prefix,
            step_id="step-a",
            input_fingerprint="input-a",
        )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("llm_timeout", True),
        ("llm_network_error", True),
        ("codex_cli_execution_failed", True),
        ("llm_profile_not_found", False),
        ("llm_json_invalid", False),
    ],
)
def test_uncertain_error_classification(
    code: str,
    expected: bool,
) -> None:
    assert provider_execution.is_uncertain_llm_error_code(code) is expected


def test_known_error_code_is_public_and_bounded() -> None:
    assert (
        provider_execution.known_provider_error_code("llm profile/not-found")
        == "VIDEO_LOCALIZATION_LLM_PROFILE_NOT_FOUND"
    )
    assert provider_execution.known_provider_error_code("") == ("VIDEO_LOCALIZATION_LLM_REQUEST_FAILED")
    assert len(provider_execution.known_provider_error_code("x" * 300)) == 127


def test_trace_response_id_uses_latest_trace() -> None:
    first = llm_runtime.LlmCompletionTrace(
        profile_id="profile-a",
        provider_host="provider.example",
        model_id="model-a",
        request_chars=100,
        request_body_bytes=120,
        max_tokens=1000,
        timeout_seconds=30,
        reasoning_effort_requested=None,
        reasoning_control_applied=True,
        duration_ms=10,
        response_id="response-a",
    )
    second = replace(first, response_id="response-b")

    assert provider_execution.trace_response_id([]) is None
    assert provider_execution.trace_response_id([first, second]) == "response-b"
