from __future__ import annotations

import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.schemas import LlmProviderListResponse, LlmProviderProfile  # noqa: E402
from app.services import llm_runtime  # noqa: E402


class FakeResponse:
    def __init__(self, payload, *, status=200, headers=None):
        self.raw = json.dumps(payload, ensure_ascii=False).encode() if not isinstance(payload, bytes) else payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.raw if size < 0 else self.raw[:size]


def _profile(**overrides):
    values = {
        "profile_id": "work",
        "name": "工作模型",
        "base_url": "https://llm.example.com/v1/",
        "model_id": "chat-model",
        "enabled": True,
        "api_key_configured": True,
    }
    values.update(overrides)
    return LlmProviderProfile(**values)


def _configure(monkeypatch, profiles=None, default="work", keys=None):
    profile_items = profiles if profiles is not None else [_profile()]
    key_values = {"work": "top-secret"} if keys is None else keys
    monkeypatch.setattr(
        llm_runtime.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=profile_items, default_profile_id=default),
    )
    monkeypatch.setattr(
        llm_runtime.settings_store,
        "llm_profile",
        lambda profile_id: next((item for item in profile_items if item.profile_id == profile_id), None),
    )
    monkeypatch.setattr(llm_runtime.settings_store, "llm_api_key", lambda profile_id: key_values.get(profile_id))


def _completion(content='{"ok": true}', **choice_overrides):
    choice = {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
    choice.update(choice_overrides)
    return {"choices": [choice]}


def test_complete_json_posts_openai_request_and_returns_object(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(_completion('{"title":"完成","items":[1]}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    result = llm_runtime.complete_json("你是整理助手", {"text": "测试"}, timeout=12)

    request = captured["request"]
    body = json.loads(request.data)
    headers = {key.lower(): value for key, value in request.header_items()}
    assert result == {"title": "完成", "items": [1]}
    assert request.full_url == "https://llm.example.com/v1/chat/completions"
    assert request.method == "POST"
    assert captured["timeout"] == 12
    assert body["model"] == "chat-model"
    assert body["messages"][0] == {"role": "system", "content": "你是整理助手"}
    assert 'JSON 数据：\n{"text": "测试"}' in body["messages"][1]["content"]
    assert "只返回 JSON" in body["messages"][1]["content"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 4096
    assert headers["authorization"] == "Bearer top-secret"
    assert headers["api-key"] == "top-secret"
    assert headers["content-type"] == "application/json"


def test_resolve_profile_uses_default_and_normalizes_url(monkeypatch):
    _configure(monkeypatch)
    resolved = llm_runtime.resolve_profile()
    assert resolved.profile_id == "work"
    assert resolved.base_url == "https://llm.example.com/v1"
    assert resolved.model_id == "chat-model"
    assert resolved.api_key == "top-secret"
    assert "top-secret" not in repr(resolved)


@pytest.mark.parametrize(
    ("profiles", "default", "profile_id", "code", "status_code"),
    [
        ([], None, None, "llm_profile_not_configured", 400),
        ([], None, "missing", "llm_profile_not_found", 404),
        ([_profile(enabled=False)], "work", None, "llm_profile_disabled", 400),
        ([_profile(model_id="")], "work", None, "llm_model_not_configured", 400),
    ],
)
def test_resolve_profile_rejects_invalid_profile_state(
    monkeypatch, profiles, default, profile_id, code, status_code
):
    _configure(monkeypatch, profiles=profiles, default=default)
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.resolve_profile(profile_id)
    assert exc_info.value.code == code
    assert exc_info.value.status_code == status_code
    assert str(exc_info.value)


def test_remote_profile_requires_key(monkeypatch):
    _configure(monkeypatch, keys={})
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.resolve_profile()
    assert exc_info.value.code == "llm_api_key_required"


def test_profile_rejects_invalid_base_url(monkeypatch):
    _configure(monkeypatch, profiles=[_profile(base_url="not-a-url")])
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.resolve_profile()
    assert exc_info.value.code == "llm_base_url_invalid"


def test_remote_profile_with_key_requires_https(monkeypatch):
    _configure(monkeypatch, profiles=[_profile(base_url="http://llm.example.com/v1")])
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.resolve_profile()
    assert exc_info.value.code == "llm_https_required"


@pytest.mark.parametrize("base_url", ["http://localhost:11434/v1", "http://127.0.0.1:11434/v1"])
def test_loopback_http_profile_allows_missing_key(monkeypatch, base_url):
    _configure(monkeypatch, profiles=[_profile(base_url=base_url, api_key_configured=False)], keys={})
    resolved = llm_runtime.resolve_profile()
    assert resolved.base_url == base_url
    assert resolved.api_key is None


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (_completion("not-json"), "llm_json_invalid"),
        (_completion("[1, 2]"), "llm_json_not_object"),
        ({"choices": []}, "llm_response_invalid"),
    ],
)
def test_complete_json_classifies_invalid_responses(monkeypatch, payload, expected_code):
    _configure(monkeypatch)
    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", lambda request, timeout: FakeResponse(payload))
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {"secret_prompt": "do-not-echo"})
    assert exc_info.value.code == expected_code
    assert "do-not-echo" not in str(exc_info.value)


def test_complete_json_can_explicitly_allow_top_level_array(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_completion('[{"segment_id":"asr_0001"}]')),
    )

    result = llm_runtime.complete_json("system", {}, allow_array=True)

    assert result == [{"segment_id": "asr_0001"}]


def test_complete_json_classifies_refusal(monkeypatch):
    _configure(monkeypatch)
    payload = _completion(None)
    payload["choices"][0]["message"]["refusal"] = "cannot comply"
    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", lambda request, timeout: FakeResponse(payload))
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == "llm_refused"
    assert exc_info.value.status_code == 422


def test_complete_json_classifies_length_finish_reason(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_completion('{"partial":', finish_reason="length")),
    )
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == "llm_output_truncated"


def test_complete_json_limits_response_bytes(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(llm_runtime, "MAX_RESPONSE_BYTES", 32)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(b"x" * 33),
    )
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == "llm_response_too_large"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "llm_auth_failed"), (403, "llm_auth_failed"), (400, "llm_http_error")],
)
def test_http_errors_are_stable_and_redacted(monkeypatch, status_code, expected_code):
    secret = "key-must-never-leak"
    prompt = "prompt-must-never-leak"
    _configure(monkeypatch, keys={"work": secret})

    def fake_urlopen(request, timeout):
        error_body = json.dumps({"error": {"message": f"bad {secret} {prompt}"}}).encode()
        raise urllib.error.HTTPError(request.full_url, status_code, "failed", {}, io.BytesIO(error_body))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json(prompt, {"value": prompt})
    error_text = str(exc_info.value)
    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == status_code
    assert secret not in error_text
    assert prompt not in error_text


@pytest.mark.parametrize("status_code", [429, 503])
def test_retryable_http_errors_retry_twice_without_real_sleep(monkeypatch, status_code):
    _configure(monkeypatch)
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, status_code, "retry", {}, io.BytesIO(b"ignored"))
        return FakeResponse(_completion('{"attempts":3}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_runtime.time, "sleep", sleeps.append)

    assert llm_runtime.complete_json("system", {}) == {"attempts": 3}
    assert len(calls) == 3
    assert sleeps == list(llm_runtime.RETRY_DELAYS)


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(429, "llm_rate_limited"), (503, "llm_provider_unavailable")],
)
def test_retry_stops_after_two_retries(monkeypatch, status_code, expected_code):
    _configure(monkeypatch)
    attempts = 0
    sleeps = []

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(request.full_url, status_code, "retry", {}, io.BytesIO(b"ignored"))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_runtime.time, "sleep", sleeps.append)
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == expected_code
    assert attempts == 3
    assert sleeps == list(llm_runtime.RETRY_DELAYS)


def test_nonrecoverable_server_error_is_not_retried(monkeypatch):
    _configure(monkeypatch)
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(request.full_url, 501, "failed", {}, io.BytesIO(b"ignored"))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        llm_runtime.time,
        "sleep",
        lambda delay: (_ for _ in ()).throw(AssertionError("不应重试不可恢复的 5xx")),
    )
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == "llm_provider_unavailable"
    assert attempts == 1


@pytest.mark.parametrize(
    ("failure", "expected_code", "status_code"),
    [
        (socket.timeout(), "llm_timeout", 504),
        (urllib.error.URLError("offline"), "llm_network_error", 502),
    ],
)
def test_transport_errors_are_classified_without_retry(monkeypatch, failure, expected_code, status_code):
    _configure(monkeypatch)
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise failure

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == status_code
    assert calls == 1
