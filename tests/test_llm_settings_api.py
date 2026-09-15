from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import codex_cli_provider, database, llm_provider, settings_store  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    return TestClient(app)


def _profile_payload(**overrides):
    payload = {
        "name": "工作模型",
        "protocol": "openai_compatible",
        "base_url": "https://llm.example.com/v1/",
        "model_id": "chat-model",
        "enabled": True,
        "api_key": "  local-secret  ",
    }
    payload.update(overrides)
    return payload


def test_llm_profile_secret_is_write_only_and_default_requires_verified_model(tmp_path: Path):
    client = _client(tmp_path)

    saved = client.put("/api/settings/llm-profiles/work", json=_profile_payload())
    assert saved.status_code == 200
    body = saved.json()
    assert body["default_profile_id"] is None
    assert body["profiles"] == [
        {
            "profile_id": "work",
            "name": "工作模型",
            "protocol": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "model_id": "chat-model",
            "reasoning_effort": "default",
            "enabled": True,
            "api_key_configured": True,
            "model_test_verified": False,
        }
    ]
    assert "local-secret" not in saved.text
    assert settings_store.llm_api_key("work") == "local-secret"
    with pytest.raises(ValueError, match="测试模型"):
        settings_store.set_default_llm_profile("work")

    verified = settings_store.mark_llm_profile_verified("work")
    assert verified.profiles[0].model_test_verified is True
    assert settings_store.set_default_llm_profile("work").default_profile_id == "work"

    client.put(
        "/api/settings/llm-profiles/backup",
        json=_profile_payload(name="备用模型", base_url="http://localhost:11434/v1", api_key=None),
    )
    deleted = client.delete("/api/settings/llm-profiles/work")
    assert deleted.status_code == 200
    assert deleted.json()["default_profile_id"] is None
    assert settings_store.llm_api_key("work") is None


def test_deleting_profile_clears_localization_phase_references(
    tmp_path: Path,
):
    client = _client(tmp_path)
    client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(),
    )
    configured = client.patch(
        "/api/settings",
        json={
            "video_localization_understanding_profile_id": "work",
            "video_localization_creation_profile_id": "work",
            "video_localization_review_profile_id": "work",
            "video_localization_alignment_profile_id": "work",
        },
    )
    assert configured.status_code == 200

    deleted = client.delete("/api/settings/llm-profiles/work")

    assert deleted.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["video_localization_understanding_profile_id"] is None
    assert settings["video_localization_creation_profile_id"] is None
    assert settings["video_localization_review_profile_id"] is None
    assert settings["video_localization_alignment_profile_id"] is None


def test_settings_reject_missing_localization_phase_profile(
    tmp_path: Path,
):
    client = _client(tmp_path)

    response = client.patch(
        "/api/settings",
        json={
            "video_localization_creation_profile_id": (
                "missing-profile"
            ),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_LLM_PROFILE_NOT_FOUND"
    )


def test_llm_profile_key_can_be_replaced_or_cleared_without_echo(tmp_path: Path):
    client = _client(tmp_path)
    client.put("/api/settings/llm-profiles/work", json=_profile_payload())

    unchanged = client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(api_key=None),
    )
    assert unchanged.json()["profiles"][0]["api_key_configured"] is True
    assert settings_store.llm_api_key("work") == "local-secret"

    cleared = client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(api_key=None, clear_api_key=True),
    )
    assert cleared.json()["profiles"][0]["api_key_configured"] is False
    assert settings_store.llm_api_key("work") is None


def test_llm_models_and_connection_use_saved_profile(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put("/api/settings/llm-profiles/work", json=_profile_payload())
    calls = []

    def fake_list_models(*, base_url, api_key, timeout=10):
        calls.append((base_url, api_key, timeout))
        return [
            {"id": "chat-model", "owned_by": "example"},
            {"id": "reasoner-model", "owned_by": None},
        ]

    monkeypatch.setattr(llm_provider, "list_models", fake_list_models)
    completion_calls = []

    def fake_complete_json(system_prompt, user_payload, **kwargs):
        completion_calls.append((system_prompt, user_payload, kwargs))
        kwargs["trace_sink"](SimpleNamespace(model_id="chat-model"))
        return {"ok": True}

    from app.services import llm_runtime

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    models = client.post("/api/settings/llm-profiles/work/models")
    assert models.status_code == 200
    assert models.json()["models"][0] == {"model_id": "chat-model", "owned_by": "example"}

    tested = client.post("/api/settings/llm-profiles/work/test")
    assert tested.status_code == 200
    assert tested.json() == {
        "profile_id": "work",
        "status": "connected",
        "models_count": None,
        "selected_model_available": True,
        "tested_model_id": "chat-model",
        "response_verified": True,
        "billing_effect": "minimal",
        "message": "模型 chat-model 已返回正确内容；本次为最小生成测试，已产生少量用量",
    }
    assert calls == [("https://llm.example.com/v1", "local-secret", 10)]
    assert completion_calls[0][1] == {"ping": "pong"}
    assert completion_calls[0][2] == {
        "profile_id": "work",
        "temperature": 0.0,
        "max_tokens": 256,
        "timeout": 45,
        "trace_sink": completion_calls[0][2]["trace_sink"],
    }
    assert settings_store.llm_profile("work").model_test_verified is True


def test_only_successfully_tested_profile_can_be_default_and_changes_revoke_it(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put("/api/settings/llm-profiles/work", json=_profile_payload())

    rejected = client.post("/api/settings/llm-profiles/work/default")
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "LLM_PROFILE_NOT_VERIFIED"

    from app.services import llm_runtime

    monkeypatch.setattr(llm_runtime, "complete_json", lambda *args, **kwargs: {"ok": True})
    assert client.post("/api/settings/llm-profiles/work/test").status_code == 200
    selected = client.post("/api/settings/llm-profiles/work/default")
    assert selected.status_code == 200
    assert selected.json()["default_profile_id"] == "work"

    changed = client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(model_id="different-model", api_key=None),
    )
    assert changed.status_code == 200
    assert changed.json()["default_profile_id"] is None
    assert changed.json()["profiles"][0]["model_test_verified"] is False
    assert client.post("/api/settings/llm-profiles/work/default").status_code == 409


def test_llm_model_test_rejects_missing_model_and_invalid_reply(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put("/api/settings/llm-profiles/work", json=_profile_payload(model_id=""))
    missing = client.post("/api/settings/llm-profiles/work/test")
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "LLM_MODEL_NOT_CONFIGURED"

    client.put("/api/settings/llm-profiles/work", json=_profile_payload())
    from app.services import llm_runtime

    monkeypatch.setattr(llm_runtime, "complete_json", lambda *args, **kwargs: {"ok": False})
    invalid = client.post("/api/settings/llm-profiles/work/test")
    assert invalid.status_code == 502
    assert invalid.json()["error"]["code"] == "LLM_MODEL_TEST_INVALID_RESPONSE"


def test_llm_api_reports_missing_profile_and_chinese_provider_error(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    missing = client.post("/api/settings/llm-profiles/missing/models")
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "未找到这个语言模型配置"

    client.put("/api/settings/llm-profiles/work", json=_profile_payload())
    monkeypatch.setattr(
        llm_provider,
        "list_models",
        lambda **kwargs: (_ for _ in ()).throw(llm_provider.LLMProviderError("API Key 无效", status_code=401)),
    )
    failed = client.post("/api/settings/llm-profiles/work/models")
    assert failed.status_code == 400
    assert failed.json()["error"]["message"] == "API Key 无效"


def test_llm_profile_rejects_invalid_base_url(tmp_path: Path):
    client = _client(tmp_path)
    response = client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(base_url="api.example.com/v1"),
    )
    assert response.status_code == 400
    assert "Base URL" in response.json()["error"]["message"]


def test_codex_profile_needs_no_url_key_or_model_and_can_be_default_after_test(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    saved = client.put(
        "/api/settings/llm-profiles/local-codex",
        json={
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "base_url": "",
            "model_id": "",
            "reasoning_effort": "default",
            "enabled": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["profiles"] == [
        {
            "profile_id": "local-codex",
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "base_url": "",
            "model_id": "",
            "reasoning_effort": "default",
            "enabled": True,
            "api_key_configured": False,
            "model_test_verified": False,
        }
    ]

    from app.services import llm_runtime

    def fake_codex_completion(*args, **kwargs):
        kwargs["trace_sink"](SimpleNamespace(model_id="gpt-default"))
        return {"ok": True}

    monkeypatch.setattr(llm_runtime, "complete_json", fake_codex_completion)
    tested = client.post("/api/settings/llm-profiles/local-codex/test")
    assert tested.status_code == 200
    assert tested.json()["tested_model_id"] == "gpt-default"
    assert "已用 gpt-default 返回正确内容" in tested.json()["message"]
    assert "ChatGPT/Codex 订阅额度" in tested.json()["message"]
    selected = client.post("/api/settings/llm-profiles/local-codex/default")
    assert selected.status_code == 200
    assert selected.json()["default_profile_id"] == "local-codex"


def test_codex_profile_persists_selected_model_and_reasoning_effort(tmp_path: Path):
    client = _client(tmp_path)

    saved = client.put(
        "/api/settings/llm-profiles/local-codex",
        json={
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "base_url": "",
            "model_id": "gpt-test",
            "reasoning_effort": "low",
            "enabled": True,
        },
    )

    assert saved.status_code == 200
    profile = saved.json()["profiles"][0]
    assert profile["model_id"] == "gpt-test"
    assert profile["reasoning_effort"] == "low"
    assert settings_store.llm_profile("local-codex").reasoning_effort == "low"


def test_switching_profile_to_codex_removes_stale_api_key(tmp_path: Path):
    client = _client(tmp_path)
    assert client.put("/api/settings/llm-profiles/work", json=_profile_payload()).status_code == 200
    assert settings_store.llm_api_key("work") == "local-secret"

    changed = client.put(
        "/api/settings/llm-profiles/work",
        json={
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "base_url": "",
            "model_id": "",
            "enabled": True,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["profiles"][0]["api_key_configured"] is False
    assert settings_store.llm_api_key("work") is None


def test_legacy_profile_without_protocol_still_loads_as_openai_compatible(tmp_path: Path):
    _client(tmp_path)
    database.apply_settings_changes(
        {
            "llm_provider_profile:legacy": (
                '{"name":"旧配置","base_url":"http://localhost:11434/v1",'
                '"model_id":"legacy-model","enabled":true}'
            )
        }
    )
    loaded = settings_store.llm_profile("legacy")
    assert loaded is not None
    assert loaded.protocol == "openai_compatible"
    assert loaded.base_url == "http://localhost:11434/v1"


def test_codex_models_use_live_subscription_catalog(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put(
        "/api/settings/llm-profiles/local-codex",
        json={
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "model_id": "",
            "enabled": True,
        },
    )
    calls = []
    monkeypatch.setattr(
        codex_cli_provider,
        "list_models",
        lambda: calls.append(True)
        or [
            {"id": "gpt-default", "owned_by": "GPT Default（Codex 默认）"},
            {"id": "gpt-fast", "owned_by": "GPT Fast"},
        ],
    )
    response = client.post("/api/settings/llm-profiles/local-codex/models")
    assert response.status_code == 200
    assert calls == [True]
    assert response.json()["models"] == [
        {"model_id": "gpt-default", "owned_by": "GPT Default（Codex 默认）"},
        {"model_id": "gpt-fast", "owned_by": "GPT Fast"},
    ]


def test_codex_auth_routes_are_local_only_and_dispatch_actions(tmp_path: Path, monkeypatch):
    database.set_db_path(tmp_path / "voice_studio.db")
    remote = TestClient(app, client=("192.168.1.20", 50000))
    for method, path in (
        ("get", "/api/settings/codex-cli/status"),
        ("post", "/api/settings/codex-cli/login"),
        ("post", "/api/settings/codex-cli/relogin"),
        ("post", "/api/settings/codex-cli/logout"),
    ):
        response = getattr(remote, method)(path)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CODEX_CLI_LOCAL_ONLY"

    local = TestClient(app)
    monkeypatch.setattr(
        codex_cli_provider,
        "status",
        lambda: codex_cli_provider.CodexCliStatus(
            installed=True,
            executable_path="/safe/codex",
            version="codex-cli 1.2.3",
            supports_image_input=True,
            logged_in=True,
            auth_type="chatgpt",
            subscription_usable=True,
        ),
    )
    login_calls = []
    monkeypatch.setattr(
        codex_cli_provider,
        "start_login",
        lambda *, relogin=False: login_calls.append(relogin),
    )
    logout_calls = []
    monkeypatch.setattr(codex_cli_provider, "logout", lambda: logout_calls.append(True))

    status = local.get("/api/settings/codex-cli/status")
    assert status.status_code == 200
    assert status.json()["supports_image_input"] is True
    assert status.json()["subscription_usable"] is True
    assert local.post("/api/settings/codex-cli/login").status_code == 200
    assert local.post("/api/settings/codex-cli/relogin").status_code == 200
    assert local.post("/api/settings/codex-cli/logout").status_code == 200
    assert login_calls == [False, True]
    assert logout_calls == [True]


def test_codex_subscription_test_is_local_only(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put(
        "/api/settings/llm-profiles/local-codex",
        json={
            "name": "本机 Codex",
            "protocol": "codex_cli",
            "model_id": "",
            "enabled": True,
        },
    )
    from app.services import llm_runtime

    calls = []
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *args, **kwargs: calls.append(True) or {"ok": True},
    )
    model_calls = []
    monkeypatch.setattr(
        codex_cli_provider,
        "list_models",
        lambda: model_calls.append(True) or [],
    )
    remote = TestClient(app, client=("10.0.0.8", 50000))
    response = remote.post("/api/settings/llm-profiles/local-codex/test")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CODEX_CLI_LOCAL_ONLY"
    models_response = remote.post("/api/settings/llm-profiles/local-codex/models")
    assert models_response.status_code == 403
    assert models_response.json()["error"]["code"] == "CODEX_CLI_LOCAL_ONLY"
    assert calls == []
    assert model_calls == []


def test_codex_auth_routes_have_local_security_openapi_descriptions(tmp_path: Path):
    client = _client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/settings/codex-cli/status": "查看本机 Codex CLI 状态",
        "/api/settings/codex-cli/login": "登录本机 Codex CLI",
        "/api/settings/codex-cli/relogin": "更换本机 Codex CLI 账号",
        "/api/settings/codex-cli/logout": "退出本机 Codex CLI",
    }
    for path, summary in expected.items():
        operation = next(iter(paths[path].values()))
        assert operation["summary"] == summary
        assert "仅允许从当前电脑访问" in operation["description"]
        assert "不读取认证文件" in operation["description"]
