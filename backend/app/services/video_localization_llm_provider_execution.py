"""Shared LLM Provider identity and replay helpers for video localization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Sequence

from app.services import llm_provider, llm_runtime
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


UNCERTAIN_LLM_ERROR_CODES = frozenset(
    {
        "llm_result_unknown",
        "llm_timeout",
        "llm_network_error",
        "llm_provider_unavailable",
        "codex_cli_timeout",
        "codex_cli_execution_failed",
    }
)
_IDEMPOTENCY_PREFIX = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


@dataclass(frozen=True)
class VideoLocalizationLlmProviderProfile:
    """Secret-free identity needed by durable Provider steps."""

    resolved: llm_runtime.ResolvedProfile
    cost_class: str
    provider_name: str
    endpoint_fingerprint: str
    configuration_fingerprint: str


def classify_provider_profile(
    resolved: llm_runtime.ResolvedProfile,
) -> VideoLocalizationLlmProviderProfile:
    if resolved.protocol == "codex_cli":
        provider_name = "codex_cli"
        cost_class = "external_paid"
        endpoint_identity = "codex_cli"
    elif llm_provider.is_local_base_url(resolved.base_url):
        provider_name = "openai_compatible_local"
        cost_class = "external_free"
        endpoint_identity = resolved.base_url
    else:
        provider_name = "openai_compatible_remote"
        cost_class = "external_paid"
        endpoint_identity = resolved.base_url
    return VideoLocalizationLlmProviderProfile(
        resolved=resolved,
        cost_class=cost_class,
        provider_name=provider_name,
        endpoint_fingerprint=hashlib.sha256(endpoint_identity.encode("utf-8")).hexdigest(),
        configuration_fingerprint=provider_configuration_fingerprint(resolved),
    )


def provider_configuration_fingerprint(
    resolved: llm_runtime.ResolvedProfile,
) -> str:
    """Fingerprint execution-affecting profile fields without secrets."""

    encoded = json.dumps(
        {
            "profile_id": resolved.profile_id,
            "protocol": resolved.protocol,
            "base_url": resolved.base_url,
            "model_id": resolved.model_id,
            "provider_model_id": resolved.provider_model_id,
            "reasoning_effort": resolved.reasoning_effort,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def provider_idempotency_key(
    execution_fence: ExecutionFence,
    *,
    key_prefix: str,
    step_id: str,
    input_fingerprint: str,
) -> str:
    """Build one stable key without retaining the step input payload."""

    normalized_prefix = str(key_prefix).strip()
    if _IDEMPOTENCY_PREFIX.fullmatch(normalized_prefix) is None:
        raise ValueError("Provider idempotency key prefix is invalid")
    payload = json.dumps(
        {
            "project_id": execution_fence.project_id,
            "operation_id": execution_fence.operation_id,
            "operation_attempt_id": execution_fence.attempt_id,
            "step_id": _required_text(step_id, "step ID"),
            "input_fingerprint": _required_text(
                input_fingerprint,
                "input fingerprint",
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{normalized_prefix}{hashlib.sha256(payload).hexdigest()}"


def is_uncertain_llm_error_code(code: str) -> bool:
    return str(code).strip() in UNCERTAIN_LLM_ERROR_CODES


def known_provider_error_code(code: str) -> str:
    """Map a Provider error to one bounded public code."""

    normalized = "".join(
        (character if character.isascii() and character.isalnum() else "_") for character in str(code).upper()
    ).strip("_")
    return (f"VIDEO_LOCALIZATION_{normalized}" if normalized else "VIDEO_LOCALIZATION_LLM_REQUEST_FAILED")[:127]


def trace_response_id(
    traces: Sequence[llm_runtime.LlmCompletionTrace],
) -> str | None:
    return traces[-1].response_id if traces else None


def _required_text(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


__all__ = [
    "UNCERTAIN_LLM_ERROR_CODES",
    "VideoLocalizationLlmProviderProfile",
    "classify_provider_profile",
    "is_uncertain_llm_error_code",
    "known_provider_error_code",
    "provider_configuration_fingerprint",
    "provider_idempotency_key",
    "trace_response_id",
]
