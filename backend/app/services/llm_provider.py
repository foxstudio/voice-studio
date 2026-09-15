from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


class LLMProviderError(RuntimeError):
    """A user-facing error raised while validating or probing an LLM provider."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _split_base_url(base_url: str) -> SplitResult:
    value = str(base_url or "").strip()
    if not value:
        raise LLMProviderError("请输入 LLM 服务的 Base URL")
    if any(char.isspace() for char in value):
        raise LLMProviderError("Base URL 不能包含空格或换行")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing port also validates malformed or out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        raise LLMProviderError("Base URL 格式无效，请检查主机名和端口") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise LLMProviderError("Base URL 只支持 http 或 https 协议")
    if not parsed.netloc or not hostname:
        raise LLMProviderError("Base URL 必须包含有效的主机名")
    if parsed.username is not None or parsed.password is not None:
        raise LLMProviderError("Base URL 不能包含用户名或密码")
    if parsed.query:
        raise LLMProviderError("Base URL 不能包含查询参数")
    if parsed.fragment:
        raise LLMProviderError("Base URL 不能包含锚点片段")
    return parsed


def normalize_base_url(base_url: str) -> str:
    """Validate and normalize a provider URL while preserving its base path."""

    parsed = _split_base_url(base_url)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def build_models_url(base_url: str) -> str:
    """Return the OpenAI-compatible models endpoint for a Base URL."""

    return normalize_base_url(base_url) + "/models"


def is_local_base_url(base_url: str) -> bool:
    """Return whether the URL points at a loopback-only local service."""

    hostname = (_split_base_url(base_url).hostname or "").lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def build_auth_headers(api_key: str | None) -> dict[str, str]:
    """Build headers accepted by OpenAI-compatible and Azure-style gateways."""

    key = str(api_key or "").strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": "VoiceStudio/1.0 LLMProvider",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["api-key"] = key
    return headers


def parse_models_payload(payload: Any) -> list[dict[str, str | None]]:
    """Extract the safe, settings-facing subset of an OpenAI model list."""

    if not isinstance(payload, dict):
        raise LLMProviderError("模型列表响应格式无效：返回内容不是 JSON 对象")
    data = payload.get("data")
    if not isinstance(data, list):
        raise LLMProviderError("模型列表响应格式无效：缺少 data 数组")

    models: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        if model_id in seen:
            continue
        owned_by = item.get("owned_by")
        models.append(
            {
                "id": model_id,
                "owned_by": owned_by.strip() if isinstance(owned_by, str) and owned_by.strip() else None,
            }
        )
        seen.add(model_id)
    return models


def _error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300] if "<" not in text else ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"].strip()[:300]
    if isinstance(error, str):
        return error.strip()[:300]
    if isinstance(payload.get("message"), str):
        return payload["message"].strip()[:300]
    return ""


def list_models(
    *,
    base_url: str,
    api_key: str | None = None,
    timeout: float = 10,
) -> list[dict[str, str | None]]:
    """Fetch models from an OpenAI-compatible provider without leaking secrets."""

    parsed = _split_base_url(base_url)
    local_service = is_local_base_url(base_url)
    key = str(api_key or "").strip()
    if not local_service and parsed.scheme.lower() != "https":
        raise LLMProviderError("远程 LLM 服务必须使用 HTTPS；只有本机服务可以使用 HTTP")
    if not key and not local_service:
        raise LLMProviderError("远程 LLM 服务需要填写 API Key；本地服务可以留空")
    if timeout <= 0:
        raise LLMProviderError("连接超时时间必须大于 0 秒")

    request = urllib.request.Request(
        build_models_url(base_url),
        headers=build_auth_headers(key),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc.read())
        suffix = f"：{detail}" if detail else ""
        raise LLMProviderError(
            f"LLM 服务请求失败（HTTP {exc.code}）{suffix}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise LLMProviderError("连接 LLM 服务超时，请检查地址、网络或服务状态") from exc
        raise LLMProviderError(f"无法连接 LLM 服务：{reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise LLMProviderError("连接 LLM 服务超时，请检查地址、网络或服务状态") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMProviderError("LLM 服务返回的模型列表不是有效 JSON") from exc
    return parse_models_payload(payload)


def test_connection(
    *,
    base_url: str,
    api_key: str | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    """Probe a provider and return a JSON-serializable result for settings APIs."""

    models = list_models(base_url=base_url, api_key=api_key, timeout=timeout)
    count = len(models)
    return {
        "ok": True,
        "message": f"连接成功，获取到 {count} 个模型",
        "model_count": count,
        "models": models,
    }
