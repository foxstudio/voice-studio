from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import llm_provider  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.raw


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/models"),
        ("https://gateway.example.com/v1/", "https://gateway.example.com/v1/models"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1/models"),
        ("http://[::1]:1234/v1/", "http://[::1]:1234/v1/models"),
    ],
)
def test_build_models_url_preserves_base_path(base_url, expected):
    assert llm_provider.build_models_url(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "api.example.com/v1",
        "ftp://api.example.com/v1",
        "https://user:pass@api.example.com/v1",
        "https://api.example.com/v1?tenant=one",
        "https://api.example.com/v1#models",
        "https://api.example.com:99999/v1",
        "https://api.example.com/v1\nInjected: yes",
    ],
)
def test_base_url_validation_rejects_unsafe_or_ambiguous_urls(base_url):
    with pytest.raises(llm_provider.LLMProviderError):
        llm_provider.build_models_url(base_url)


def test_list_models_sends_dual_auth_headers_and_parses_safe_fields(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "object": "list",
                "data": [
                    {"id": "deepseek-chat", "owned_by": "deepseek", "private": "ignore"},
                    {"id": "deepseek-reasoner", "owned_by": 42},
                    {"id": "deepseek-chat", "owned_by": "duplicate"},
                    {"owned_by": "missing-id"},
                    "invalid",
                ],
            }
        )

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", fake_urlopen)

    result = llm_provider.list_models(
        base_url="https://api.deepseek.com/v1/",
        api_key="secret-key",
        timeout=7,
    )

    request = captured["request"]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.full_url == "https://api.deepseek.com/v1/models"
    assert request.method == "GET"
    assert headers["authorization"] == "Bearer secret-key"
    assert headers["api-key"] == "secret-key"
    assert captured["timeout"] == 7
    assert result == [
        {"id": "deepseek-chat", "owned_by": "deepseek"},
        {"id": "deepseek-reasoner", "owned_by": None},
    ]


@pytest.mark.parametrize("base_url", ["http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1"])
def test_local_services_allow_requests_without_api_key(base_url, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = {name.lower(): value for name, value in request.header_items()}
        return FakeResponse({"data": []})

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", fake_urlopen)

    assert llm_provider.list_models(base_url=base_url) == []
    assert "authorization" not in captured["headers"]
    assert "api-key" not in captured["headers"]


def test_remote_service_requires_api_key_without_making_request(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("缺少 Key 时不应发起网络请求")

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", unexpected_request)

    with pytest.raises(llm_provider.LLMProviderError, match="远程 LLM 服务需要填写 API Key"):
        llm_provider.list_models(base_url="https://api.example.com/v1")


def test_remote_http_service_never_receives_api_key(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("远程明文 HTTP 地址不应收到任何请求")

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", unexpected_request)

    with pytest.raises(llm_provider.LLMProviderError, match="远程 LLM 服务必须使用 HTTPS"):
        llm_provider.list_models(base_url="http://api.example.com/v1", api_key="do-not-send")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "不是 JSON 对象"),
        ({}, "缺少 data 数组"),
        ({"data": {}}, "缺少 data 数组"),
    ],
)
def test_parse_models_payload_rejects_invalid_shapes(payload, message):
    with pytest.raises(llm_provider.LLMProviderError, match=message):
        llm_provider.parse_models_payload(payload)


def test_list_models_reports_chinese_http_error_without_leaking_key(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"message":"Invalid API key"}}'),
        )

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_provider.LLMProviderError, match="LLM 服务请求失败（HTTP 401）") as exc_info:
        llm_provider.list_models(base_url="https://api.example.com/v1", api_key="do-not-leak")

    assert exc_info.value.status_code == 401
    assert "Invalid API key" in str(exc_info.value)
    assert "do-not-leak" not in str(exc_info.value)


def test_list_models_reports_invalid_json(monkeypatch):
    monkeypatch.setattr(
        llm_provider.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )

    with pytest.raises(llm_provider.LLMProviderError, match="不是有效 JSON"):
        llm_provider.list_models(base_url="http://localhost:11434/v1")


def test_connection_returns_settings_friendly_result(monkeypatch):
    monkeypatch.setattr(
        llm_provider.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse({"data": [{"id": "model-a", "owned_by": "local"}]}),
    )

    result = llm_provider.test_connection(base_url="http://localhost:11434/v1")

    assert result == {
        "ok": True,
        "message": "连接成功，获取到 1 个模型",
        "model_count": 1,
        "models": [{"id": "model-a", "owned_by": "local"}],
    }
