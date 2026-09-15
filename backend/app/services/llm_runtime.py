from __future__ import annotations

import base64
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable, Literal, Sequence
from urllib.parse import urlsplit

from app.services import codex_cli_provider, llm_provider, settings_store


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_IMAGE_COUNT = 8
MAX_IMAGE_TOTAL_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 2
RETRY_DELAYS = (0.2, 0.5)
SAFE_RETRY_STATUS_CODES = frozenset({429})
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
ReasoningEffort = Literal["low", "high", "max"]


class LlmRuntimeError(RuntimeError):
    """A stable, user-facing failure from the structured LLM runtime."""

    def __init__(self, message: str, *, code: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedProfile:
    profile_id: str
    protocol: Literal["openai_compatible", "codex_cli"]
    base_url: str
    model_id: str
    provider_model_id: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LlmImageInput:
    """One validated image supplied to a multimodal completion."""

    data: bytes = field(repr=False)
    media_type: Literal["image/png", "image/jpeg", "image/webp"]


@dataclass(frozen=True)
class LlmCompletionTrace:
    """Safe completion metadata without prompts, outputs, or hidden reasoning."""

    profile_id: str
    model_id: str
    provider_host: str
    request_chars: int
    request_body_bytes: int
    max_tokens: int
    timeout_seconds: float
    reasoning_effort_requested: ReasoningEffort | None
    reasoning_control_applied: bool
    duration_ms: int
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    content_chars: int = 0
    reasoning_chars: int = 0
    response_id: str | None = None
    error_code: str | None = None


TraceSink = Callable[[LlmCompletionTrace], None]


class _ResponseStatusError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


def resolve_profile(profile_id: str | None = None) -> ResolvedProfile:
    """Resolve and validate a saved profile for an LLM completion request."""

    profiles = settings_store.llm_profiles()
    selected_id = str(profile_id or "").strip() or profiles.default_profile_id
    if not selected_id:
        raise LlmRuntimeError(
            "尚未配置默认语言模型",
            code="llm_profile_not_configured",
            status_code=400,
        )

    profile = settings_store.llm_profile(selected_id)
    if profile is None:
        raise LlmRuntimeError(
            "未找到指定的语言模型配置",
            code="llm_profile_not_found",
            status_code=404,
        )
    if not profile.enabled:
        raise LlmRuntimeError(
            "所选语言模型配置已停用",
            code="llm_profile_disabled",
            status_code=400,
        )

    model_id = profile.model_id.strip()
    profile_reasoning_effort = (
        None
        if profile.reasoning_effort == "default"
        else profile.reasoning_effort
    )
    if profile.protocol == "openai_compatible" and not model_id:
        raise LlmRuntimeError(
            "所选语言模型配置尚未选择模型",
            code="llm_model_not_configured",
            status_code=400,
        )

    if profile.protocol == "codex_cli":
        return ResolvedProfile(
            profile_id=selected_id,
            protocol="codex_cli",
            base_url="",
            model_id=model_id or "codex-default",
            provider_model_id=model_id or None,
            reasoning_effort=profile_reasoning_effort,
        )

    try:
        base_url = llm_provider.normalize_base_url(profile.base_url)
        local_service = llm_provider.is_local_base_url(base_url)
    except llm_provider.LLMProviderError:
        raise LlmRuntimeError(
            "所选语言模型配置的 Base URL 无效",
            code="llm_base_url_invalid",
            status_code=400,
        ) from None

    api_key = settings_store.llm_api_key(selected_id)
    api_key = api_key.strip() if api_key and api_key.strip() else None
    if not local_service and not api_key:
        raise LlmRuntimeError(
            "远程语言模型服务需要配置 API Key",
            code="llm_api_key_required",
            status_code=400,
        )
    if not local_service and urlsplit(base_url).scheme != "https":
        raise LlmRuntimeError(
            "远程语言模型服务必须使用 HTTPS",
            code="llm_https_required",
            status_code=400,
        )

    return ResolvedProfile(
        profile_id=selected_id,
        protocol="openai_compatible",
        base_url=base_url,
        model_id=model_id,
        reasoning_effort=profile_reasoning_effort,
        api_key=api_key,
    )


def complete_json(
    system_prompt: str,
    user_payload: dict,
    profile_id: str | None = None,
    temperature: float | None = 0.0,
    max_tokens: int = 4096,
    timeout: float = 90,
    allow_array: bool = False,
    disable_reasoning: bool = False,
    reasoning_effort: ReasoningEffort | None = None,
    trace_sink: TraceSink | None = None,
    idempotency_key: str | None = None,
    resolved_profile: ResolvedProfile | None = None,
) -> dict | list:
    """Run a structured completion and return validated JSON.

    OpenAI-compatible transports receive a caller-owned ``Idempotency-Key``.
    Codex CLI has no equivalent transport header; callers must still use the
    same key in their durable local replay barrier.
    """

    if not isinstance(user_payload, dict):
        raise LlmRuntimeError(
            "提交给语言模型的数据必须是 JSON 对象",
            code="llm_payload_invalid",
            status_code=400,
        )
    if timeout <= 0:
        raise LlmRuntimeError("请求超时时间必须大于 0 秒", code="llm_timeout_invalid", status_code=400)
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise LlmRuntimeError("max_tokens 必须是正整数", code="llm_max_tokens_invalid", status_code=400)
    _validate_reasoning_effort(reasoning_effort)
    normalized_idempotency_key = _validate_idempotency_key(
        idempotency_key
    )

    try:
        payload_json = json.dumps(user_payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise LlmRuntimeError(
            "提交给语言模型的数据无法序列化为 JSON",
            code="llm_payload_invalid",
            status_code=400,
        ) from None

    profile = resolved_profile or resolve_profile(profile_id)
    if (
        profile_id
        and profile.profile_id != str(profile_id).strip()
    ):
        raise ValueError(
            "resolved LLM profile differs from requested profile"
        )
    effective_reasoning_effort = (
        reasoning_effort or profile.reasoning_effort
    )
    output_shape = "JSON 对象或数组" if allow_array else "JSON 对象"
    user_content = (
        f"请处理以下 JSON 数据。\nJSON 数据：\n{payload_json}\n\n只返回 {output_shape}，不要返回 Markdown、解释或其他文本。"
    )
    if profile.protocol == "codex_cli":
        started_at = time.monotonic()
        error_code: str | None = None
        completion: codex_cli_provider.CodexCompletion | None = None
        prompt = _codex_prompt(str(system_prompt), user_content)
        try:
            completion = _codex_complete(
                prompt,
                profile=profile,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
            )
            return _parse_json_text(completion.text, allow_array=allow_array)
        except LlmRuntimeError as exc:
            error_code = exc.code
            raise
        finally:
            _emit_codex_trace(
                trace_sink,
                profile=profile,
                completion=completion,
                request_chars=len(str(system_prompt)) + len(user_content),
                request_body_bytes=len(prompt.encode("utf-8")),
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
                started_at=started_at,
                error_code=error_code,
            )
    request_body = {
        "model": profile.model_id,
        "messages": [
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        request_body["temperature"] = temperature
    reasoning_control_applied = _apply_reasoning_control(
        request_body,
        profile,
        disable_reasoning=disable_reasoning,
        reasoning_effort=effective_reasoning_effort,
    )
    headers = llm_provider.build_auth_headers(profile.api_key)
    headers["Content-Type"] = "application/json"
    if normalized_idempotency_key is not None:
        headers["Idempotency-Key"] = normalized_idempotency_key
    request = _completion_request(profile.base_url, request_body, headers)
    sent_request = request
    started_at = time.monotonic()
    raw: bytes | None = None
    error_code: str | None = None
    try:
        try:
            raw = _send_with_retries(request, timeout)
        except LlmRuntimeError as exc:
            if exc.code != "llm_json_object_unsupported":
                raise
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                raise LlmRuntimeError(
                    "连接语言模型服务超时，请稍后重试",
                    code="llm_timeout",
                    status_code=504,
                ) from None
            fallback_body = dict(request_body)
            fallback_body.pop("response_format", None)
            sent_request = _completion_request(
                profile.base_url,
                fallback_body,
                headers,
            )
            raw = _send_with_retries(sent_request, remaining)
        return _parse_completion(raw, allow_array=allow_array)
    except LlmRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _emit_completion_trace(
            trace_sink,
            profile=profile,
            raw=raw,
            request_chars=len(str(system_prompt)) + len(user_content),
            request_body_bytes=len(sent_request.data or b""),
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=effective_reasoning_effort,
            reasoning_control_applied=reasoning_control_applied,
            started_at=started_at,
            error_code=error_code,
        )


def complete_text(
    system_prompt: str,
    user_content: str,
    profile_id: str | None = None,
    temperature: float | None = 0.0,
    max_tokens: int = 4096,
    timeout: float = 90,
    disable_reasoning: bool = False,
    reasoning_effort: ReasoningEffort | None = None,
    trace_sink: TraceSink | None = None,
) -> str:
    """Run an OpenAI-compatible completion and return plain assistant text."""

    if not isinstance(user_content, str) or not user_content.strip():
        raise LlmRuntimeError(
            "提交给语言模型的文本不能为空",
            code="llm_payload_invalid",
            status_code=400,
        )
    _validate_completion_limits(
        max_tokens=max_tokens,
        timeout=timeout,
    )
    _validate_reasoning_effort(reasoning_effort)

    profile = resolve_profile(profile_id)
    effective_reasoning_effort = (
        reasoning_effort or profile.reasoning_effort
    )
    if profile.protocol == "codex_cli":
        started_at = time.monotonic()
        error_code: str | None = None
        completion: codex_cli_provider.CodexCompletion | None = None
        prompt = _codex_prompt(
            str(system_prompt),
            user_content.strip(),
        )
        try:
            completion = _codex_complete(
                prompt,
                profile=profile,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
            )
            return completion.text.strip()
        except LlmRuntimeError as exc:
            error_code = exc.code
            raise
        finally:
            _emit_codex_trace(
                trace_sink,
                profile=profile,
                completion=completion,
                request_chars=(
                    len(str(system_prompt)) + len(user_content.strip())
                ),
                request_body_bytes=len(prompt.encode("utf-8")),
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
                started_at=started_at,
                error_code=error_code,
            )
    request_body = {
        "model": profile.model_id,
        "messages": [
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": user_content.strip()},
        ],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        request_body["temperature"] = temperature
    reasoning_control_applied = _apply_reasoning_control(
        request_body,
        profile,
        disable_reasoning=disable_reasoning,
        reasoning_effort=effective_reasoning_effort,
    )
    headers = llm_provider.build_auth_headers(profile.api_key)
    headers["Content-Type"] = "application/json"
    request = _completion_request(
        profile.base_url,
        request_body,
        headers,
    )
    started_at = time.monotonic()
    raw: bytes | None = None
    error_code: str | None = None
    try:
        raw = _send_with_retries(request, timeout)
        return _parse_text_completion(raw)
    except LlmRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _emit_completion_trace(
            trace_sink,
            profile=profile,
            raw=raw,
            request_chars=(
                len(str(system_prompt)) + len(user_content.strip())
            ),
            request_body_bytes=len(request.data or b""),
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=effective_reasoning_effort,
            reasoning_control_applied=reasoning_control_applied,
            started_at=started_at,
            error_code=error_code,
        )


def complete_multimodal_json(
    system_prompt: str,
    user_payload: dict,
    images: Sequence[LlmImageInput],
    profile_id: str | None = None,
    temperature: float | None = 0.0,
    max_tokens: int = 4096,
    timeout: float = 90,
    allow_array: bool = False,
    disable_reasoning: bool = False,
    reasoning_effort: ReasoningEffort | None = None,
    trace_sink: TraceSink | None = None,
    idempotency_key: str | None = None,
    resolved_profile: ResolvedProfile | None = None,
) -> dict | list:
    """Run a JSON completion with validated inline PNG, JPEG, or WebP images.

    OpenAI-compatible transports receive a caller-owned ``Idempotency-Key``.
    Callers that already locked a profile may pass it directly so a durable
    task never re-resolves mutable settings between submission and execution.
    """

    payload_json = _serialize_json_payload(user_payload)
    validated_images = _validate_image_inputs(images)
    _validate_completion_limits(max_tokens=max_tokens, timeout=timeout)
    _validate_reasoning_effort(reasoning_effort)
    normalized_idempotency_key = _validate_idempotency_key(
        idempotency_key
    )

    profile = resolved_profile or resolve_profile(profile_id)
    if (
        profile_id
        and profile.profile_id != str(profile_id).strip()
    ):
        raise ValueError(
            "resolved LLM profile differs from requested profile"
        )
    effective_reasoning_effort = (
        reasoning_effort or profile.reasoning_effort
    )
    output_shape = "JSON 对象或数组" if allow_array else "JSON 对象"
    codex_user_text = (
        f"请结合图片处理以下 JSON 数据。\nJSON 数据：\n{payload_json}"
        f"\n\n只返回 {output_shape}，不要返回 Markdown、解释或其他文本。"
    )
    if profile.protocol == "codex_cli":
        started_at = time.monotonic()
        error_code: str | None = None
        completion: codex_cli_provider.CodexCompletion | None = None
        prompt = _codex_prompt(str(system_prompt), codex_user_text)
        try:
            completion = _codex_complete(
                prompt,
                profile=profile,
                images=validated_images,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
            )
            return _parse_json_text(completion.text, allow_array=allow_array)
        except LlmRuntimeError as exc:
            error_code = exc.code
            raise
        finally:
            _emit_codex_trace(
                trace_sink,
                profile=profile,
                completion=completion,
                request_chars=len(str(system_prompt)) + len(codex_user_text),
                request_body_bytes=len(prompt.encode("utf-8")),
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=effective_reasoning_effort,
                started_at=started_at,
                error_code=error_code,
            )
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": codex_user_text,
        }
    ]
    user_content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{image.media_type};base64,{base64.b64encode(image.data).decode('ascii')}",
            },
        }
        for image in validated_images
    )
    request_body = {
        "model": profile.model_id,
        "messages": [
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        request_body["temperature"] = temperature
    reasoning_control_applied = _apply_reasoning_control(
        request_body,
        profile,
        disable_reasoning=disable_reasoning,
        reasoning_effort=effective_reasoning_effort,
    )
    headers = llm_provider.build_auth_headers(profile.api_key)
    headers["Content-Type"] = "application/json"
    if normalized_idempotency_key is not None:
        headers["Idempotency-Key"] = normalized_idempotency_key
    request = _completion_request(
        profile.base_url,
        request_body,
        headers,
    )
    sent_request = request
    started_at = time.monotonic()
    raw: bytes | None = None
    error_code: str | None = None
    try:
        try:
            raw = _send_with_retries(
                request,
                timeout,
                image_request=True,
            )
        except LlmRuntimeError as exc:
            if exc.code != "llm_json_object_unsupported":
                raise
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                raise LlmRuntimeError(
                    "连接语言模型服务超时，请稍后重试",
                    code="llm_timeout",
                    status_code=504,
                ) from None
            fallback_body = dict(request_body)
            fallback_body.pop("response_format", None)
            sent_request = _completion_request(
                profile.base_url,
                fallback_body,
                headers,
            )
            raw = _send_with_retries(
                sent_request,
                remaining,
                image_request=True,
            )
        return _parse_completion(raw, allow_array=allow_array)
    except LlmRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _emit_completion_trace(
            trace_sink,
            profile=profile,
            raw=raw,
            request_chars=(
                len(str(system_prompt))
                + len(str(user_content[0]["text"]))
            ),
            request_body_bytes=len(sent_request.data or b""),
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=effective_reasoning_effort,
            reasoning_control_applied=reasoning_control_applied,
            started_at=started_at,
            error_code=error_code,
        )


def _codex_prompt(system_prompt: str, user_content: str) -> str:
    return (
        "请直接完成下面的语言模型任务。不要调用任何工具，不要访问网络或文件系统。\n\n"
        f"系统指令：\n{system_prompt}\n\n"
        f"用户内容：\n{user_content}"
    )


def _codex_complete(
    prompt: str,
    *,
    profile: ResolvedProfile,
    images: Sequence[LlmImageInput] = (),
    timeout: float,
    reasoning_effort: ReasoningEffort | None,
) -> codex_cli_provider.CodexCompletion:
    try:
        return codex_cli_provider.complete(
            prompt,
            model_id=profile.provider_model_id or "",
            image_inputs=[(image.data, image.media_type) for image in images],
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        )
    except codex_cli_provider.CodexCliError as exc:
        raise LlmRuntimeError(
            str(exc),
            code=exc.code,
            status_code=exc.status_code,
        ) from exc


def _parse_json_text(content: str, *, allow_array: bool) -> dict | list:
    normalized = content.strip()
    candidates = [normalized]
    fenced_parts = normalized.split("```")
    for index in range(1, len(fenced_parts), 2):
        fenced = fenced_parts[index].strip()
        if fenced.casefold().startswith("json"):
            fenced = fenced[4:].lstrip("\r\n ")
        if fenced:
            candidates.append(fenced)
    opening = min(
        (
            position
            for position in (
                normalized.find("{"),
                normalized.find("["),
            )
            if position >= 0
        ),
        default=-1,
    )
    if opening >= 0:
        closing_character = "}" if normalized[opening] == "{" else "]"
        closing = normalized.rfind(closing_character)
        if closing > opening:
            candidates.append(normalized[opening : closing + 1])
    result: Any = None
    for candidate in dict.fromkeys(candidates):
        try:
            result = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if result is None:
        raise LlmRuntimeError(
            "语言模型未返回有效 JSON",
            code="llm_json_invalid",
            status_code=502,
        ) from None
    if isinstance(result, list) and allow_array:
        return result
    if not isinstance(result, dict):
        raise LlmRuntimeError(
            "语言模型返回的 JSON 不是对象",
            code="llm_json_not_object",
            status_code=502,
        )
    return result


def _emit_codex_trace(
    trace_sink: TraceSink | None,
    *,
    profile: ResolvedProfile,
    completion: codex_cli_provider.CodexCompletion | None,
    request_chars: int,
    request_body_bytes: int,
    max_tokens: int,
    timeout: float,
    reasoning_effort: ReasoningEffort | None,
    started_at: float,
    error_code: str | None,
) -> None:
    if trace_sink is None:
        return
    try:
        trace_sink(
            LlmCompletionTrace(
                profile_id=profile.profile_id,
                model_id=(
                    completion.model_id
                    if completion is not None and completion.model_id
                    else profile.model_id or "codex-default"
                ),
                provider_host="local-codex-cli",
                request_chars=max(0, request_chars),
                request_body_bytes=max(0, request_body_bytes),
                max_tokens=max_tokens,
                timeout_seconds=timeout,
                reasoning_effort_requested=reasoning_effort,
                reasoning_control_applied=reasoning_effort is not None,
                duration_ms=max(
                    0,
                    int(round((time.monotonic() - started_at) * 1000)),
                ),
                finish_reason=completion.finish_reason if completion is not None else None,
                prompt_tokens=completion.prompt_tokens if completion is not None else None,
                cached_tokens=completion.cached_tokens if completion is not None else None,
                completion_tokens=completion.completion_tokens if completion is not None else None,
                total_tokens=completion.total_tokens if completion is not None else None,
                content_chars=len(completion.text) if completion is not None else 0,
                error_code=error_code,
            )
        )
    except Exception:
        return


def _serialize_json_payload(user_payload: dict) -> str:
    if not isinstance(user_payload, dict):
        raise LlmRuntimeError(
            "提交给语言模型的数据必须是 JSON 对象",
            code="llm_payload_invalid",
            status_code=400,
        )
    try:
        return json.dumps(user_payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise LlmRuntimeError(
            "提交给语言模型的数据无法序列化为 JSON",
            code="llm_payload_invalid",
            status_code=400,
        ) from None


def _validate_completion_limits(*, max_tokens: int, timeout: float) -> None:
    if timeout <= 0:
        raise LlmRuntimeError("请求超时时间必须大于 0 秒", code="llm_timeout_invalid", status_code=400)
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise LlmRuntimeError("max_tokens 必须是正整数", code="llm_max_tokens_invalid", status_code=400)


def _validate_image_inputs(images: Sequence[LlmImageInput]) -> tuple[LlmImageInput, ...]:
    if isinstance(images, (str, bytes, bytearray)) or not isinstance(images, Sequence):
        raise LlmRuntimeError(
            "图片输入必须是图片列表",
            code="llm_images_invalid",
            status_code=400,
        )
    validated = tuple(images)
    if not validated:
        raise LlmRuntimeError(
            "多模态请求至少需要一张图片",
            code="llm_images_required",
            status_code=400,
        )
    if len(validated) > MAX_IMAGE_COUNT:
        raise LlmRuntimeError(
            f"单次请求最多允许 {MAX_IMAGE_COUNT} 张图片",
            code="llm_image_count_exceeded",
            status_code=400,
        )

    total_bytes = 0
    for image in validated:
        if not isinstance(image, LlmImageInput):
            raise LlmRuntimeError(
                "图片输入类型无效",
                code="llm_images_invalid",
                status_code=400,
            )
        if image.media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
            raise LlmRuntimeError(
                "图片仅支持 PNG、JPEG 或 WebP 格式",
                code="llm_image_type_unsupported",
                status_code=400,
            )
        if not isinstance(image.data, bytes) or not image.data:
            raise LlmRuntimeError(
                "图片内容不能为空",
                code="llm_image_data_invalid",
                status_code=400,
            )
        total_bytes += len(image.data)
        if total_bytes > MAX_IMAGE_TOTAL_BYTES:
            raise LlmRuntimeError(
                f"图片总大小不能超过 {MAX_IMAGE_TOTAL_BYTES // (1024 * 1024)} MB",
                code="llm_image_total_size_exceeded",
                status_code=400,
            )
    return validated


def _supports_thinking_control(profile: ResolvedProfile) -> bool:
    """Return whether the provider accepts DeepSeek's thinking toggle."""

    hostname = (urlsplit(profile.base_url).hostname or "").casefold()
    return hostname == "deepseek.com" or hostname.endswith(".deepseek.com")


def _validate_reasoning_effort(
    reasoning_effort: ReasoningEffort | None,
) -> None:
    if reasoning_effort not in {None, "low", "high", "max"}:
        raise LlmRuntimeError(
            "reasoning_effort 仅支持 low、high 或 max",
            code="llm_reasoning_effort_invalid",
            status_code=400,
        )


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or not value.isascii()
        or any(character.isspace() for character in value)
        or any(
            not (
                character.isalnum()
                or character in {"-", "_", ".", ":", "/"}
            )
            for character in value
        )
    ):
        raise LlmRuntimeError(
            "语言模型请求幂等键格式无效",
            code="llm_idempotency_key_invalid",
            status_code=400,
        )
    return value


def _apply_reasoning_control(
    request_body: dict[str, Any],
    profile: ResolvedProfile,
    *,
    disable_reasoning: bool,
    reasoning_effort: ReasoningEffort | None,
) -> bool:
    hostname = (urlsplit(profile.base_url).hostname or "").casefold()
    if reasoning_effort is not None and (
        hostname == "openrouter.ai"
        or hostname.endswith(".openrouter.ai")
    ):
        request_body["reasoning"] = {
            "effort": reasoning_effort,
            "exclude": True,
        }
        return True
    if reasoning_effort is not None and (
        hostname == "moonshot.ai"
        or hostname.endswith(".moonshot.ai")
        or hostname == "moonshot.cn"
        or hostname.endswith(".moonshot.cn")
    ):
        request_body["reasoning_effort"] = reasoning_effort
        return True
    if disable_reasoning and _supports_thinking_control(profile):
        request_body["thinking"] = {"type": "disabled"}
        return True
    return False


def _emit_completion_trace(
    trace_sink: TraceSink | None,
    *,
    profile: ResolvedProfile,
    raw: bytes | None,
    request_chars: int,
    request_body_bytes: int,
    max_tokens: int,
    timeout: float,
    reasoning_effort: ReasoningEffort | None,
    reasoning_control_applied: bool,
    started_at: float,
    error_code: str | None,
) -> None:
    if trace_sink is None:
        return
    try:
        payload = _trace_response_payload(raw)
        choice = _trace_choice(payload)
        message = (
            choice.get("message")
            if isinstance(choice.get("message"), dict)
            else {}
        )
        usage = (
            payload.get("usage")
            if isinstance(payload.get("usage"), dict)
            else {}
        )
        prompt_details = (
            usage.get("prompt_tokens_details")
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else {}
        )
        completion_details = (
            usage.get("completion_tokens_details")
            if isinstance(
                usage.get("completion_tokens_details"),
                dict,
            )
            else {}
        )
        content = message.get("content")
        reasoning = (
            message.get("reasoning")
            if isinstance(message.get("reasoning"), str)
            else message.get("reasoning_content")
        )
        trace_sink(
            LlmCompletionTrace(
                profile_id=profile.profile_id,
                model_id=profile.model_id,
                provider_host=(
                    urlsplit(profile.base_url).hostname or ""
                ).casefold(),
                request_chars=max(0, request_chars),
                request_body_bytes=max(0, request_body_bytes),
                max_tokens=max_tokens,
                timeout_seconds=timeout,
                reasoning_effort_requested=reasoning_effort,
                reasoning_control_applied=reasoning_control_applied,
                duration_ms=max(
                    0,
                    int(round((time.monotonic() - started_at) * 1000)),
                ),
                finish_reason=_optional_string(
                    choice.get("finish_reason")
                ),
                native_finish_reason=_optional_string(
                    choice.get("native_finish_reason")
                ),
                prompt_tokens=_optional_int(
                    usage.get("prompt_tokens")
                ),
                cached_tokens=_optional_int(
                    prompt_details.get("cached_tokens")
                ),
                completion_tokens=_optional_int(
                    usage.get("completion_tokens")
                ),
                reasoning_tokens=_optional_int(
                    completion_details.get("reasoning_tokens")
                ),
                total_tokens=_optional_int(
                    usage.get("total_tokens")
                ),
                cost_usd=_optional_float(usage.get("cost")),
                content_chars=(
                    len(content) if isinstance(content, str) else 0
                ),
                reasoning_chars=(
                    len(reasoning)
                    if isinstance(reasoning, str)
                    else 0
                ),
                response_id=_optional_string(payload.get("id")),
                error_code=error_code,
            )
        )
    except Exception:
        # Observability must never change the completion outcome.
        return


def _trace_response_payload(raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _trace_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if (
        isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
    ):
        return choices[0]
    return {}


def _optional_int(value: Any) -> int | None:
    return (
        int(value)
        if isinstance(value, int) and not isinstance(value, bool)
        else None
    )


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _completion_request(base_url: str, request_body: dict[str, Any], headers: dict[str, str]) -> urllib.request.Request:
    encoded_body = json.dumps(request_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return urllib.request.Request(
        base_url + "/chat/completions",
        data=encoded_body,
        headers=headers,
        method="POST",
    )


def _send_with_retries(
    request: urllib.request.Request,
    timeout: float,
    *,
    image_request: bool = False,
) -> bytes:
    deadline = time.monotonic() + timeout
    for attempt in range(MAX_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LlmRuntimeError(
                "连接语言模型服务超时，请稍后重试",
                code="llm_timeout",
                status_code=504,
            )
        error_body = b""
        try:
            attempt_timeout = timeout if attempt == 0 else remaining
            with urllib.request.urlopen(request, timeout=attempt_timeout) as response:
                status_code = int(getattr(response, "status", 200))
                if status_code >= 400:
                    raise _ResponseStatusError(status_code)
                return _read_limited(response)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            error_body = _read_error_body(exc)
            exc.close()
        except _ResponseStatusError as exc:
            status_code = exc.status_code
        except (http.client.IncompleteRead, http.client.RemoteDisconnected):
            raise LlmRuntimeError(
                "语言模型响应中途断开，结果是否生成无法确认；"
                "为避免重复计费，未自动重试",
                code="llm_result_unknown",
                status_code=502,
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LlmRuntimeError(
                    "连接语言模型服务超时，请稍后重试",
                    code="llm_timeout",
                    status_code=504,
                ) from None
            raise LlmRuntimeError(
                "无法连接语言模型服务，请检查网络或服务状态",
                code="llm_network_error",
                status_code=502,
            ) from None
        except (TimeoutError, socket.timeout):
            raise LlmRuntimeError(
                "连接语言模型服务超时，请稍后重试",
                code="llm_timeout",
                status_code=504,
            ) from None
        except OSError:
            raise LlmRuntimeError(
                "无法连接语言模型服务，请检查网络或服务状态",
                code="llm_network_error",
                status_code=502,
            ) from None

        if (
            status_code in SAFE_RETRY_STATUS_CODES
            and _wait_before_retry(attempt, deadline)
        ):
            continue
        raise _http_error(status_code, error_body, image_request=image_request)

    raise AssertionError("unreachable")


def _wait_before_retry(attempt: int, deadline: float) -> bool:
    if attempt >= MAX_RETRIES:
        return False
    delay = RETRY_DELAYS[attempt]
    if time.monotonic() + delay >= deadline:
        raise LlmRuntimeError(
            "连接语言模型服务超时，请稍后重试",
            code="llm_timeout",
            status_code=504,
        )
    time.sleep(delay)
    return True


def _read_limited(response: BinaryIO) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if headers is not None else None
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise _response_too_large()
        except ValueError:
            pass

    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    except TypeError:
        raw = response.read()
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise _response_too_large()
    return raw


def _read_error_body(response: BinaryIO) -> bytes:
    try:
        raw = response.read(64 * 1024)
    except (OSError, TypeError, ValueError):
        return b""
    return raw if isinstance(raw, bytes) else b""


def _http_error(
    status_code: int,
    error_body: bytes = b"",
    *,
    image_request: bool = False,
) -> LlmRuntimeError:
    normalized_error = error_body.decode("utf-8", "ignore").casefold()
    if status_code == 402 and any(
        marker in normalized_error
        for marker in (
            "insufficient credits",
            "insufficient_credit",
            "add credits",
        )
    ):
        return LlmRuntimeError(
            "语言模型服务余额不足，请充值或改用其他模型配置",
            code="llm_insufficient_credits",
            status_code=402,
        )
    context_markers = (
        "context length",
        "context_length",
        "maximum context",
        "max context",
        "input is too long",
        "prompt is too long",
        "too many tokens",
    )
    if status_code in {400, 413, 422} and any(marker in normalized_error for marker in context_markers):
        return LlmRuntimeError(
            "提交给语言模型的内容超过上下文长度限制",
            code="llm_context_too_long",
            status_code=status_code,
        )
    if status_code == 400 and "json_object" in normalized_error and "does not support" in normalized_error:
        return LlmRuntimeError(
            "当前模型不支持 JSON Object 响应格式，已尝试兼容模式",
            code="llm_json_object_unsupported",
            status_code=400,
        )
    image_markers = (
        "image input",
        "image_url",
        "vision",
        "multimodal",
        "does not support images",
        "image is not supported",
    )
    if image_request and status_code in {400, 413, 415, 422} and any(
        marker in normalized_error for marker in image_markers
    ):
        return LlmRuntimeError(
            "当前语言模型不支持图片输入",
            code="llm_image_input_unsupported",
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return LlmRuntimeError(
            "语言模型服务鉴权失败，请检查 API Key",
            code="llm_auth_failed",
            status_code=status_code,
        )
    if status_code == 429:
        return LlmRuntimeError(
            "语言模型服务请求过于频繁，请稍后重试",
            code="llm_rate_limited",
            status_code=429,
        )
    if 500 <= status_code <= 599:
        return LlmRuntimeError(
            "语言模型服务暂时不可用，请稍后重试",
            code="llm_provider_unavailable",
            status_code=status_code,
        )
    return LlmRuntimeError(
        f"语言模型服务请求失败（HTTP {status_code}）",
        code="llm_http_error",
        status_code=status_code,
    )


def _response_too_large() -> LlmRuntimeError:
    return LlmRuntimeError(
        "语言模型服务返回的数据过大",
        code="llm_response_too_large",
        status_code=502,
    )


def _parse_completion(raw: bytes, *, allow_array: bool = False) -> dict | list:
    try:
        response_payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LlmRuntimeError(
            "语言模型服务返回了无效响应",
            code="llm_response_invalid",
            status_code=502,
        ) from None

    if not isinstance(response_payload, dict):
        raise _invalid_response()
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _invalid_response()

    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _invalid_response()
    if message.get("refusal") or choice.get("refusal"):
        raise LlmRuntimeError(
            "语言模型拒绝了这次请求",
            code="llm_refused",
            status_code=422,
        )
    if choice.get("finish_reason") == "length":
        raise LlmRuntimeError(
            "语言模型输出因长度限制而不完整",
            code="llm_output_truncated",
            status_code=502,
        )

    content = message.get("content")
    if not isinstance(content, str):
        raise _invalid_response()
    return _parse_json_text(content, allow_array=allow_array)


def _parse_text_completion(raw: bytes) -> str:
    try:
        response_payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid_response() from None
    if not isinstance(response_payload, dict):
        raise _invalid_response()
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _invalid_response()
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _invalid_response()
    if message.get("refusal") or choice.get("refusal"):
        raise LlmRuntimeError(
            "语言模型拒绝了这次请求",
            code="llm_refused",
            status_code=422,
        )
    if choice.get("finish_reason") == "length":
        raise LlmRuntimeError(
            "语言模型输出因长度限制而不完整",
            code="llm_output_truncated",
            status_code=502,
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _invalid_response()
    return content.strip()


def _invalid_response() -> LlmRuntimeError:
    return LlmRuntimeError(
        "语言模型服务返回了无效响应",
        code="llm_response_invalid",
        status_code=502,
    )
