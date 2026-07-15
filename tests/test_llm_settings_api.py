from __future__ import annotations

import sys
from pathlib import Path

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
        "make_default": True,
    }
    payload.update(overrides)
    return payload


def test_llm_profile_secret_is_write_only_and_default_is_maintained(tmp_path: Path):
    client = _client(tmp_path)

    saved = client.put("/api/settings/llm-profiles/work", json=_profile_payload())
    assert saved.status_code == 200
    body = saved.json()
    assert body["default_profile_id"] == "work"
    assert body["profiles"] == [
        {
            "profile_id": "work",
            "name": "工作模型",
            "protocol": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "model_id": "chat-model",
            "enabled": True,
            "api_key_configured": True,
        }
    ]
    assert "local-secret" not in saved.text
    assert settings_store.llm_api_key("work") == "local-secret"

    client.put(
        "/api/settings/llm-profiles/backup",
        json=_profile_payload(name="备用模型", base_url="http://localhost:11434/v1", api_key=None, make_default=False),
    )
    deleted = client.delete("/api/settings/llm-profiles/work")
    assert deleted.status_code == 200
    assert deleted.json()["default_profile_id"] == "backup"
    assert settings_store.llm_api_key("work") is None


def test_llm_profile_key_can_be_replaced_or_cleared_without_echo(tmp_path: Path):
    client = _client(tmp_path)
    client.put("/api/settings/llm-profiles/work", json=_profile_payload())

    unchanged = client.put(
        "/api/settings/llm-profiles/work",
        json=_profile_payload(api_key=None, make_default=False),
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
    monkeypatch.setattr(
        llm_provider,
        "test_connection",
        lambda **kwargs: {
            "ok": True,
            "message": "连接成功，获取到 2 个模型",
            "model_count": 2,
            "models": fake_list_models(**kwargs),
        },
    )

    models = client.post("/api/settings/llm-profiles/work/models")
    assert models.status_code == 200
    assert models.json()["models"][0] == {"model_id": "chat-model", "owned_by": "example"}

    tested = client.post("/api/settings/llm-profiles/work/test")
    assert tested.status_code == 200
    assert tested.json() == {
        "profile_id": "work",
        "status": "connected",
        "models_count": 2,
        "selected_model_available": True,
        "message": "连接成功，获取到 2 个模型",
    }
    assert all(call[:2] == ("https://llm.example.com/v1", "local-secret") for call in calls)


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
