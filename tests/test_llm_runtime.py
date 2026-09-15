from __future__ import annotations

import http.client
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


def test_complete_json_uses_caller_pinned_resolved_profile(
    monkeypatch,
):
    profile = llm_runtime.ResolvedProfile(
        profile_id="pinned",
        protocol="openai_compatible",
        base_url="https://pinned.example.com/v1",
        model_id="pinned-model",
        api_key="pinned-secret",
    )
    captured = {}

    def unexpected_resolve(_profile_id=None):
        raise AssertionError("pinned profile must not be resolved again")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(_completion('{"ok": true}'))

    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        unexpected_resolve,
    )
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = llm_runtime.complete_json(
        "system",
        {"value": 1},
        profile_id="pinned",
        resolved_profile=profile,
        timeout=12,
    )

    assert result == {"ok": True}
    assert captured["request"].full_url == (
        "https://pinned.example.com/v1/chat/completions"
    )
    assert json.loads(captured["request"].data)["model"] == (
        "pinned-model"
    )
    pinned_headers = {
        key.lower(): value
        for key, value in captured["request"].header_items()
    }
    assert pinned_headers["authorization"] == (
        "Bearer pinned-secret"
    )


def test_complete_json_sends_bounded_provider_idempotency_key(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = {
            key.lower(): value
            for key, value in request.header_items()
        }
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    assert llm_runtime.complete_json(
        "system",
        {},
        idempotency_key="vsl_semgrp_123",
    ) == {"ok": True}
    assert (
        captured["headers"]["idempotency-key"]
        == "vsl_semgrp_123"
    )


@pytest.mark.parametrize(
    "value",
    ["", "contains whitespace", "unsafe\nheader", "x" * 201],
)
def test_complete_json_rejects_invalid_idempotency_key_before_network(
    monkeypatch,
    value,
):
    _configure(monkeypatch)
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    with pytest.raises(
        llm_runtime.LlmRuntimeError,
        match="幂等",
    ) as exc_info:
        llm_runtime.complete_json(
            "system",
            {},
            idempotency_key=value,
        )

    assert exc_info.value.code == "llm_idempotency_key_invalid"
    assert calls == 0


def test_complete_json_disables_reasoning_for_direct_deepseek_profile(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[_profile(base_url="https://api.deepseek.com", model_id="deepseek-v4-flash")],
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_json("system", {}, disable_reasoning=True) == {"ok": True}
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_complete_json_does_not_send_provider_specific_reasoning_toggle(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_json("system", {}, disable_reasoning=True) == {"ok": True}
    assert "thinking" not in captured["body"]


def test_complete_json_uses_low_reasoning_for_openrouter_and_reports_usage(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                base_url="https://openrouter.ai/api/v1",
                model_id="moonshotai/kimi-k3",
                reasoning_effort="low",
            )
        ],
    )
    captured = {}
    traces = []

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(
            {
                "id": "generation-123",
                "provider": "MoonshotAI",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"ok":true}',
                            "reasoning": "private reasoning text",
                        },
                        "finish_reason": "stop",
                        "native_finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1815,
                    "completion_tokens": 612,
                    "total_tokens": 2427,
                    "cost": 0.0132426,
                    "prompt_tokens_details": {"cached_tokens": 512},
                    "completion_tokens_details": {"reasoning_tokens": 372},
                },
            }
        )

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    result = llm_runtime.complete_json(
        "system",
        {"question": "enough?"},
        trace_sink=traces.append,
    )

    assert result == {"ok": True}
    assert captured["body"]["reasoning"] == {
        "effort": "low",
        "exclude": True,
    }
    assert len(traces) == 1
    trace = traces[0]
    assert trace.profile_id == "work"
    assert trace.model_id == "moonshotai/kimi-k3"
    assert trace.provider_host == "openrouter.ai"
    assert trace.reasoning_effort_requested == "low"
    assert trace.reasoning_control_applied is True
    assert trace.finish_reason == "stop"
    assert trace.native_finish_reason == "stop"
    assert trace.prompt_tokens == 1815
    assert trace.cached_tokens == 512
    assert trace.completion_tokens == 612
    assert trace.reasoning_tokens == 372
    assert trace.total_tokens == 2427
    assert trace.cost_usd == pytest.approx(0.0132426)
    assert trace.content_chars == len('{"ok":true}')
    assert trace.reasoning_chars == len("private reasoning text")
    assert trace.response_id == "generation-123"
    assert trace.error_code is None
    assert not hasattr(trace, "reasoning")


def test_complete_json_uses_low_reasoning_for_direct_moonshot(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                base_url="https://api.moonshot.ai/v1",
                model_id="kimi-k3",
                reasoning_effort="low",
            )
        ],
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_json(
        "system",
        {},
    ) == {"ok": True}
    assert captured["body"]["reasoning_effort"] == "low"


def test_complete_json_uses_provider_default_temperature_when_none(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_json("system", {}, temperature=None) == {"ok": True}
    assert "temperature" not in captured["body"]


def test_complete_text_posts_plain_prompt_without_json_mode(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[_profile(base_url="https://api.deepseek.com", model_id="deepseek-v4-flash")],
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse(_completion("  自然中文全文。  "))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    result = llm_runtime.complete_text(
        "你是翻译助手",
        "请翻译完整正文。",
        temperature=0.4,
        max_tokens=60000,
        timeout=12,
        disable_reasoning=True,
    )

    assert result == "自然中文全文。"
    assert captured["timeout"] == 12
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "你是翻译助手"},
        {"role": "user", "content": "请翻译完整正文。"},
    ]
    assert captured["body"]["temperature"] == 0.4
    assert captured["body"]["max_tokens"] == 60000
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "response_format" not in captured["body"]


def test_complete_text_uses_provider_default_temperature_when_none(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion("自然中文全文。"))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_text("system", "content", temperature=None) == "自然中文全文。"
    assert "temperature" not in captured["body"]


def test_complete_text_rejects_truncated_output(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda _request, timeout: FakeResponse(_completion("partial", finish_reason="length")),
    )

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_text("system", "content")

    assert exc_info.value.code == "llm_output_truncated"


def test_complete_json_falls_back_when_provider_rejects_json_object(monkeypatch):
    _configure(monkeypatch)
    requests = []
    timeouts = []
    now = [0.0]

    def fake_urlopen(request, timeout):
        requests.append(request)
        timeouts.append(timeout)
        if len(requests) == 1:
            now[0] = 0.4
            error_body = json.dumps(
                {
                    "error": {
                        "message": "Provider returned error",
                        "metadata": {"raw": "Model does not support 'json_object' response format."},
                    }
                }
            ).encode()
            raise urllib.error.HTTPError(request.full_url, 400, "failed", {}, io.BytesIO(error_body))
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_json("system", {}, timeout=1) == {"ok": True}
    assert len(requests) == 2
    assert timeouts == pytest.approx([1.0, 0.6])
    assert json.loads(requests[0].data)["response_format"] == {"type": "json_object"}
    assert "response_format" not in json.loads(requests[1].data)


def test_complete_multimodal_json_posts_inline_images_and_returns_object(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse(_completion('{"scene":"访谈"}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    result = llm_runtime.complete_multimodal_json(
        "你是画面分析助手",
        {"task": "识别画面"},
        [
            llm_runtime.LlmImageInput(data=b"\x89PNG\r\n", media_type="image/png"),
            llm_runtime.LlmImageInput(data=b"RIFFwebp", media_type="image/webp"),
        ],
        timeout=12,
    )

    content = captured["body"]["messages"][1]["content"]
    assert result == {"scene": "访谈"}
    assert captured["timeout"] == 12
    assert content[0]["type"] == "text"
    assert 'JSON 数据：\n{"task": "识别画面"}' in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,iVBORw0K"},
    }
    assert content[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/webp;base64,UklGRndlYnA="},
    }
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_complete_multimodal_json_uses_locked_profile_and_idempotency_key(
    monkeypatch,
):
    _configure(monkeypatch)
    captured = {}
    locked = llm_runtime.ResolvedProfile(
        profile_id="locked-vision",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:18080/v1",
        model_id="vision-model",
    )

    def reject_reresolve(_profile_id=None):
        raise AssertionError("locked multimodal profile was re-resolved")

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Idempotency-key")
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime, "resolve_profile", reject_reresolve)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    assert llm_runtime.complete_multimodal_json(
        "system",
        {"task": "vision"},
        [
            llm_runtime.LlmImageInput(
                data=b"jpeg",
                media_type="image/jpeg",
            )
        ],
        profile_id="locked-vision",
        resolved_profile=locked,
        idempotency_key="visual-call-1",
    ) == {"ok": True}
    assert captured["authorization"] == "visual-call-1"
    assert captured["body"]["model"] == "vision-model"


def test_complete_multimodal_json_rejects_mismatched_locked_profile():
    locked = llm_runtime.ResolvedProfile(
        profile_id="locked-vision",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:18080/v1",
        model_id="vision-model",
    )

    with pytest.raises(ValueError, match="differs"):
        llm_runtime.complete_multimodal_json(
            "system",
            {"task": "vision"},
            [
                llm_runtime.LlmImageInput(
                    data=b"jpeg",
                    media_type="image/jpeg",
                )
            ],
            profile_id="other-profile",
            resolved_profile=locked,
        )


def test_complete_multimodal_json_reports_usage_with_low_reasoning(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                base_url="https://openrouter.ai/api/v1",
                model_id="moonshotai/kimi-k3",
                reasoning_effort="low",
            )
        ],
    )
    captured = {}
    traces = []

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(
            {
                "id": "vision-generation",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"scene":"访谈"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 900,
                    "completion_tokens": 120,
                    "total_tokens": 1020,
                    "cost": 0.0045,
                    "completion_tokens_details": {
                        "reasoning_tokens": 40
                    },
                },
            }
        )

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    result = llm_runtime.complete_multimodal_json(
        "system",
        {"task": "识图"},
        [
            llm_runtime.LlmImageInput(
                data=b"\x89PNG\r\n",
                media_type="image/png",
            )
        ],
        trace_sink=traces.append,
    )

    assert result == {"scene": "访谈"}
    assert captured["body"]["reasoning"] == {
        "effort": "low",
        "exclude": True,
    }
    assert traces[0].prompt_tokens == 900
    assert traces[0].reasoning_tokens == 40
    assert traces[0].cost_usd == 0.0045


def test_complete_multimodal_json_rejects_unsupported_image_type_before_request(monkeypatch):
    _configure(monkeypatch)
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise AssertionError("非法图片不应发送给服务端")

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_multimodal_json(
            "system",
            {},
            [llm_runtime.LlmImageInput(data=b"GIF89a", media_type="image/gif")],
        )

    assert exc_info.value.code == "llm_image_type_unsupported"
    assert calls == 0


def test_complete_multimodal_json_rejects_image_count_and_total_size_limits(monkeypatch):
    _configure(monkeypatch)
    image = llm_runtime.LlmImageInput(data=b"x", media_type="image/jpeg")

    with pytest.raises(llm_runtime.LlmRuntimeError) as count_error:
        llm_runtime.complete_multimodal_json("system", {}, [image] * (llm_runtime.MAX_IMAGE_COUNT + 1))
    assert count_error.value.code == "llm_image_count_exceeded"

    monkeypatch.setattr(llm_runtime, "MAX_IMAGE_TOTAL_BYTES", 3)
    with pytest.raises(llm_runtime.LlmRuntimeError) as size_error:
        llm_runtime.complete_multimodal_json(
            "system",
            {},
            [
                llm_runtime.LlmImageInput(data=b"xx", media_type="image/jpeg"),
                llm_runtime.LlmImageInput(data=b"xx", media_type="image/png"),
            ],
        )
    assert size_error.value.code == "llm_image_total_size_exceeded"


def test_complete_multimodal_json_classifies_provider_without_image_support(monkeypatch):
    _configure(monkeypatch)

    def fake_urlopen(request, timeout):
        error_body = json.dumps({"error": {"message": "This model does not support image input"}}).encode()
        raise urllib.error.HTTPError(request.full_url, 400, "failed", {}, io.BytesIO(error_body))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_multimodal_json(
            "system",
            {},
            [llm_runtime.LlmImageInput(data=b"\xff\xd8\xff", media_type="image/jpeg")],
        )

    assert exc_info.value.code == "llm_image_input_unsupported"
    assert exc_info.value.status_code == 400


def test_complete_multimodal_json_keeps_images_when_falling_back_from_json_object(monkeypatch):
    _configure(monkeypatch)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            error_body = json.dumps(
                {"error": {"message": "Model does not support 'json_object' response format."}}
            ).encode()
            raise urllib.error.HTTPError(request.full_url, 400, "failed", {}, io.BytesIO(error_body))
        return FakeResponse(_completion('{"ok":true}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    assert llm_runtime.complete_multimodal_json(
        "system",
        {},
        [llm_runtime.LlmImageInput(data=b"\x89PNG", media_type="image/png")],
    ) == {"ok": True}
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    assert requests[1]["messages"][1]["content"][1] == requests[0]["messages"][1]["content"][1]


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
def test_resolve_profile_rejects_invalid_profile_state(monkeypatch, profiles, default, profile_id, code, status_code):
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
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse(_completion('[{"segment_id":"asr_0001"}]'))

    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = llm_runtime.complete_json("system", {}, allow_array=True)

    assert result == [{"segment_id": "asr_0001"}]
    assert "只返回 JSON 对象或数组" in captured["body"]["messages"][1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"chunk_id":"chunk_0001"}\n```',
        '结果如下：\n{"chunk_id":"chunk_0001"}\n请查收。',
    ],
)
def test_complete_json_accepts_one_unambiguous_wrapped_object(
    monkeypatch,
    content,
):
    _configure(monkeypatch)
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_completion(content)),
    )

    assert llm_runtime.complete_json("system", {}) == {
        "chunk_id": "chunk_0001"
    }


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


def test_complete_json_reports_usage_when_output_is_truncated(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                base_url="https://openrouter.ai/api/v1",
                model_id="moonshotai/kimi-k3",
            )
        ],
    )
    traces = []
    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(
            {
                "id": "generation-truncated",
                "provider": "MoonshotAI",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"partial":',
                            "reasoning": "reasoning omitted from storage",
                        },
                        "finish_reason": "length",
                        "native_finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1500,
                    "completion_tokens": 2400,
                    "total_tokens": 3900,
                    "cost": 0.0405,
                    "completion_tokens_details": {
                        "reasoning_tokens": 2200
                    },
                },
            }
        ),
    )

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json(
            "system",
            {},
            max_tokens=2400,
            reasoning_effort="low",
            trace_sink=traces.append,
        )

    assert exc_info.value.code == "llm_output_truncated"
    assert len(traces) == 1
    trace = traces[0]
    assert trace.finish_reason == "length"
    assert trace.completion_tokens == 2400
    assert trace.reasoning_tokens == 2200
    assert trace.cost_usd == pytest.approx(0.0405)
    assert trace.error_code == "llm_output_truncated"
    assert trace.content_chars == len('{"partial":')
    assert trace.reasoning_chars == len("reasoning omitted from storage")
    assert not hasattr(trace, "content")


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
    [
        (401, "llm_auth_failed"),
        (403, "llm_auth_failed"),
        (400, "llm_http_error"),
        (402, "llm_http_error"),
    ],
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


def test_complete_json_classifies_insufficient_provider_credits(monkeypatch):
    _configure(monkeypatch)

    def fake_urlopen(request, timeout):
        error_body = json.dumps(
            {"error": {"message": "Insufficient credits. Add credits."}}
        ).encode()
        raise urllib.error.HTTPError(
            request.full_url,
            402,
            "failed",
            {},
            io.BytesIO(error_body),
        )

    monkeypatch.setattr(
        llm_runtime.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})

    assert exc_info.value.code == "llm_insufficient_credits"
    assert exc_info.value.status_code == 402
    assert "余额不足" in str(exc_info.value)


def test_complete_json_classifies_explicit_context_length_errors(monkeypatch):
    _configure(monkeypatch)

    def fake_urlopen(request, timeout):
        error_body = json.dumps(
            {"error": {"message": "This model's maximum context length was exceeded"}}
        ).encode()
        raise urllib.error.HTTPError(request.full_url, 400, "failed", {}, io.BytesIO(error_body))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {"value": "large document"})

    assert exc_info.value.code == "llm_context_too_long"
    assert "large document" not in str(exc_info.value)


def test_explicit_rate_limit_retries_twice_without_real_sleep(
    monkeypatch,
):
    _configure(monkeypatch)
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 3:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "retry",
                {},
                io.BytesIO(b"ignored"),
            )
        return FakeResponse(_completion('{"attempts":3}'))

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_runtime.time, "sleep", sleeps.append)

    assert llm_runtime.complete_json("system", {}) == {"attempts": 3}
    assert len(calls) == 3
    assert sleeps == list(llm_runtime.RETRY_DELAYS)


@pytest.mark.parametrize(
    "failure",
    [
        http.client.IncompleteRead(b""),
        http.client.RemoteDisconnected(),
    ],
)
def test_ambiguous_response_disconnect_is_not_replayed(
    monkeypatch,
    failure,
):
    _configure(monkeypatch)
    calls = []
    sleeps = []
    traces = []

    class IncompleteResponse(FakeResponse):
        def read(self, size=-1):
            raise failure

    def fake_urlopen(request, timeout):
        calls.append(request)
        if isinstance(failure, http.client.IncompleteRead):
            return IncompleteResponse(b"")
        raise failure

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_runtime.time, "sleep", sleeps.append)

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json(
            "system",
            {},
            trace_sink=traces.append,
        )
    assert exc_info.value.code == "llm_result_unknown"
    assert len(calls) == 1
    assert sleeps == []
    assert len(traces) == 1
    assert traces[0].error_code == "llm_result_unknown"


def test_rate_limit_retry_stops_after_two_retries(monkeypatch):
    _configure(monkeypatch)
    attempts = 0
    sleeps = []

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "retry",
            {},
            io.BytesIO(b"ignored"),
        )

    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_runtime.time, "sleep", sleeps.append)
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {})
    assert exc_info.value.code == "llm_rate_limited"
    assert attempts == 3
    assert sleeps == list(llm_runtime.RETRY_DELAYS)


def test_retry_timeout_is_one_total_deadline(monkeypatch):
    _configure(monkeypatch)
    now = [0.0]
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 1
        now[0] = 0.9
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "retry",
            {},
            io.BytesIO(b"ignored"),
        )

    monkeypatch.setattr(llm_runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(llm_runtime.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_json("system", {}, timeout=1)

    assert exc_info.value.code == "llm_timeout"
    assert attempts == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_ambiguous_server_error_is_not_replayed(
    monkeypatch,
    status_code,
):
    _configure(monkeypatch)
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url,
            status_code,
            "failed",
            {},
            io.BytesIO(b"ignored"),
        )

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


def test_codex_profile_allows_default_model_and_dispatches_text(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    captured = {}

    def fake_complete(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return llm_runtime.codex_cli_provider.CodexCompletion(text="自然中文全文。")

    monkeypatch.setattr(llm_runtime.codex_cli_provider, "complete", fake_complete)

    resolved = llm_runtime.resolve_profile()
    assert resolved.model_id == "codex-default"
    assert resolved.provider_model_id is None

    result = llm_runtime.complete_text(
        "你是翻译助手",
        "翻译正文",
        timeout=12,
        disable_reasoning=True,
    )

    assert result == "自然中文全文。"
    assert "系统指令：\n你是翻译助手" in captured["prompt"]
    assert "用户内容：\n翻译正文" in captured["prompt"]
    assert captured["kwargs"]["model_id"] == ""
    assert captured["kwargs"]["timeout"] == 12
    assert captured["kwargs"]["reasoning_effort"] is None


def test_codex_json_accepts_fenced_output_and_emits_compatible_trace(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="gpt-codex",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    monkeypatch.setattr(
        llm_runtime.codex_cli_provider,
        "complete",
        lambda *args, **kwargs: llm_runtime.codex_cli_provider.CodexCompletion(
            text='```json\n{"ok":true}\n```',
            model_id="gpt-codex",
            finish_reason="stop",
            prompt_tokens=12,
            cached_tokens=2,
            completion_tokens=4,
            total_tokens=16,
        ),
    )
    traces = []

    result = llm_runtime.complete_json(
        "system",
        {"ping": "pong"},
        trace_sink=traces.append,
    )

    assert result == {"ok": True}
    assert traces[0].provider_host == "local-codex-cli"
    assert traces[0].model_id == "gpt-codex"
    assert traces[0].prompt_tokens == 12
    assert traces[0].cached_tokens == 2
    assert traces[0].completion_tokens == 4
    assert traces[0].total_tokens == 16
    assert traces[0].response_id is None


def test_codex_json_accepts_fenced_object_with_brief_surrounding_text(
    monkeypatch,
):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="gpt-codex",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    monkeypatch.setattr(
        llm_runtime.codex_cli_provider,
        "complete",
        lambda *args, **kwargs: (
            llm_runtime.codex_cli_provider.CodexCompletion(
                text='结果如下：\n```json\n{"ok":true}\n```\n请查收。',
            )
        ),
    )

    assert llm_runtime.complete_json("system", {"ping": "pong"}) == {
        "ok": True
    }


def test_codex_uses_profile_model_and_explicit_callsite_reasoning(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="gpt-settings",
                reasoning_effort="low",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    captured = {}

    def fake_complete(prompt, **kwargs):
        captured.update(kwargs)
        return llm_runtime.codex_cli_provider.CodexCompletion(
            text='{"ok":true}',
            model_id="gpt-settings",
        )

    monkeypatch.setattr(llm_runtime.codex_cli_provider, "complete", fake_complete)

    assert llm_runtime.complete_json(
        "system",
        {"ping": "pong"},
        reasoning_effort="high",
    ) == {"ok": True}
    assert captured["model_id"] == "gpt-settings"
    assert captured["reasoning_effort"] == "high"


def test_codex_multimodal_dispatches_validated_image_bytes(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    captured = {}

    def fake_complete(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return llm_runtime.codex_cli_provider.CodexCompletion(text='{"scene":"访谈"}')

    monkeypatch.setattr(llm_runtime.codex_cli_provider, "complete", fake_complete)

    result = llm_runtime.complete_multimodal_json(
        "识别画面",
        {"task": "关键帧"},
        [llm_runtime.LlmImageInput(data=b"png-data", media_type="image/png")],
    )

    assert result == {"scene": "访谈"}
    assert captured["kwargs"]["image_inputs"] == [(b"png-data", "image/png")]
    assert 'JSON 数据：\n{"task": "关键帧"}' in captured["prompt"]


def test_codex_errors_keep_stable_runtime_contract(monkeypatch):
    _configure(
        monkeypatch,
        profiles=[
            _profile(
                protocol="codex_cli",
                base_url="",
                model_id="",
                api_key_configured=False,
            )
        ],
        keys={},
    )
    monkeypatch.setattr(
        llm_runtime.codex_cli_provider,
        "complete",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            llm_runtime.codex_cli_provider.CodexCliError(
                "Codex CLI 登录已失效，请重新登录 ChatGPT（安全诊断：terminal=error;exit_code=1）",
                code="codex_cli_not_logged_in",
                status_code=401,
            )
        ),
    )
    with pytest.raises(llm_runtime.LlmRuntimeError) as exc_info:
        llm_runtime.complete_text("system", "content")
    assert exc_info.value.code == "codex_cli_not_logged_in"
    assert exc_info.value.status_code == 401
    assert "安全诊断" in str(exc_info.value)
