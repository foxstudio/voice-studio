from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import database, llm_provider, settings_store  # noqa: E402


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
        "message": "模型 chat-model 响应正常；本次为最小生成测试，已产生少量用量",
    }
    assert calls == [("https://llm.example.com/v1", "local-secret", 10)]
    assert completion_calls[0][1] == {"ping": "pong"}
    assert completion_calls[0][2] == {
        "profile_id": "work",
        "temperature": 0.0,
        "max_tokens": 256,
        "timeout": 45,
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
