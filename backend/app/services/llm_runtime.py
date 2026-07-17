from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from app.services import llm_provider, settings_store


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RETRIES = 2
RETRY_DELAYS = (0.2, 0.5)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class LlmRuntimeError(RuntimeError):
    """A stable, user-facing failure from the structured LLM runtime."""

    def __init__(self, message: str, *, code: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedProfile:
    profile_id: str
    base_url: str
    model_id: str
    api_key: str | None = field(default=None, repr=False)


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
    if not model_id:
        raise LlmRuntimeError(
            "所选语言模型配置尚未选择模型",
            code="llm_model_not_configured",
            status_code=400,
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
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
    )


def complete_json(
    system_prompt: str,
    user_payload: dict,
    profile_id: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: float = 90,
    allow_array: bool = False,
    disable_reasoning: bool = False,
) -> dict | list:
    """Run an OpenAI-compatible completion and return validated JSON."""

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

    try:
        payload_json = json.dumps(user_payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise LlmRuntimeError(
            "提交给语言模型的数据无法序列化为 JSON",
            code="llm_payload_invalid",
            status_code=400,
        ) from None

    profile = resolve_profile(profile_id)
    output_shape = "JSON 对象或数组" if allow_array else "JSON 对象"
    user_content = (
        f"请处理以下 JSON 数据。\nJSON 数据：\n{payload_json}\n\n只返回 {output_shape}，不要返回 Markdown、解释或其他文本。"
    )
    request_body = {
        "model": profile.model_id,
        "messages": [
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if disable_reasoning and _supports_thinking_control(profile):
        request_body["thinking"] = {"type": "disabled"}
    headers = llm_provider.build_auth_headers(profile.api_key)
    headers["Content-Type"] = "application/json"
    request = _completion_request(profile.base_url, request_body, headers)
    started_at = time.monotonic()
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
        raw = _send_with_retries(_completion_request(profile.base_url, fallback_body, headers), remaining)
    return _parse_completion(raw, allow_array=allow_array)


def _supports_thinking_control(profile: ResolvedProfile) -> bool:
    """Return whether the provider accepts DeepSeek's thinking toggle."""

    hostname = (urlsplit(profile.base_url).hostname or "").casefold()
    return hostname == "deepseek.com" or hostname.endswith(".deepseek.com")


def _completion_request(base_url: str, request_body: dict[str, Any], headers: dict[str, str]) -> urllib.request.Request:
    encoded_body = json.dumps(request_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return urllib.request.Request(
        base_url + "/chat/completions",
        data=encoded_body,
        headers=headers,
        method="POST",
    )


def _send_with_retries(request: urllib.request.Request, timeout: float) -> bytes:
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

        if status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt]
            if time.monotonic() + delay >= deadline:
                raise LlmRuntimeError(
                    "连接语言模型服务超时，请稍后重试",
                    code="llm_timeout",
                    status_code=504,
                )
            time.sleep(delay)
            continue
        raise _http_error(status_code, error_body)

    raise AssertionError("unreachable")


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


def _http_error(status_code: int, error_body: bytes = b"") -> LlmRuntimeError:
    normalized_error = error_body.decode("utf-8", "ignore").casefold()
    if status_code == 400 and "json_object" in normalized_error and "does not support" in normalized_error:
        return LlmRuntimeError(
            "当前模型不支持 JSON Object 响应格式，已尝试兼容模式",
            code="llm_json_object_unsupported",
            status_code=400,
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
    try:
        result: Any = json.loads(content)
    except json.JSONDecodeError:
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


def _invalid_response() -> LlmRuntimeError:
    return LlmRuntimeError(
        "语言模型服务返回了无效响应",
        code="llm_response_invalid",
        status_code=502,
    )
