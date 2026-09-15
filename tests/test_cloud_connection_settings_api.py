from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import (  # noqa: E402
    cloud_connection_tests,
    database,
    doubao_client,
    doubao_speaker_catalog_store,
    llm_provider,
)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("VOLCENGINE_SECRET_ACCESS_KEY", raising=False)
    return TestClient(app)


@pytest.mark.parametrize("provider", ["mimo", "doubao", "volcengine_directory"])
def test_cloud_connection_requires_saved_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str):
    client = _client(tmp_path, monkeypatch)
    response = client.post(f"/api/settings/cloud-connections/{provider}/test")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CLOUD_CREDENTIALS_REQUIRED"


@pytest.mark.parametrize(
    "base_url",
    ["https://api.xiaomimimo.com/v1", "https://token-plan-cn.xiaomimimo.com/v1"],
)
def test_mimo_connection_uses_saved_key_and_official_models_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/settings", json={"mimo_base_url": base_url})
    client.patch("/api/settings/mimo-secret", json={"api_key": "mimo-secret"})
    captured: dict[str, object] = {}

    def fake_list_models(*, base_url: str, api_key: str, timeout: int):
        captured.update(base_url=base_url, api_key=api_key, timeout=timeout)
        return [{"id": "mimo-v2.5", "owned_by": "mimo"}]

    monkeypatch.setattr(llm_provider, "list_models", fake_list_models)
    response = client.post("/api/settings/cloud-connections/mimo/test")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "mimo",
        "status": "connected",
        "message": "MiMo 连接正常，获取到 1 个可用模型",
        "verified_scopes": ["models"],
        "billing_effect": "none",
        "models_count": 1,
        "request_id": None,
        "logid": None,
    }
    assert captured == {"base_url": base_url, "api_key": "mimo-secret", "timeout": 12}
    assert "mimo-secret" not in response.text


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.xiaomimimo.com/v1",
        "https://example.com/v1",
        "https://user:pass@api.xiaomimimo.com/v1",
        "https://api.xiaomimimo.com/v1?tenant=one",
    ],
)
def test_mimo_connection_rejects_unsafe_test_url_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/settings", json={"mimo_base_url": base_url})
    client.patch("/api/settings/mimo-secret", json={"api_key": "mimo-secret"})
    monkeypatch.setattr(
        llm_provider,
        "list_models",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unsafe URL must not reach network")),
    )
    response = client.post("/api/settings/cloud-connections/mimo/test")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CLOUD_BASE_URL_INVALID"
    assert "mimo-secret" not in response.text


def test_doubao_connection_only_claims_tts_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(tmp_path, monkeypatch)
    client.patch(
        "/api/settings",
        json={"doubao_default_tts_resource_id": "seed-tts-custom"},
    )
    client.patch("/api/settings/doubao-secret", json={"api_key": "doubao-secret"})
    captured: dict[str, object] = {}

    def fake_probe(**kwargs):
        captured.update(kwargs)
        return {"request_id": "request-1", "logid": "log-1", "audio_bytes": 24}

    monkeypatch.setattr(doubao_client, "probe_tts_connection", fake_probe)
    response = client.post("/api/settings/cloud-connections/doubao/test")
    body = response.json()
    assert response.status_code == 200
    assert body["verified_scopes"] == ["tts"]
    assert body["billing_effect"] == "minimal"
    assert body["request_id"] == "request-1"
    assert "复刻、训练与 Seed Audio 权限未在本次测试中验证" in body["message"]
    assert captured["resource_id"] == "seed-tts-custom"
    assert captured["api_key"] == "doubao-secret"
    assert "doubao-secret" not in response.text


def test_volcengine_directory_probe_is_one_read_only_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(tmp_path, monkeypatch)
    client.patch(
        "/api/settings/volcengine-directory-secret",
        json={"access_key_id": "test-ak", "secret_access_key": "test-sk"},
    )
    captured: dict[str, object] = {}

    def fake_signed_request(self, action, *, query=None, body=None, now=None, timeout=15):
        captured.update(action=action, body=body, timeout=timeout, access_key=self.access_key)
        return {"ResponseMetadata": {"RequestId": "volc-request"}, "Result": {"Speakers": []}}

    monkeypatch.setattr(doubao_speaker_catalog_store.VolcengineOpenAPIClient, "signed_request", fake_signed_request)
    response = client.post("/api/settings/cloud-connections/volcengine_directory/test")
    assert response.status_code == 200
    assert response.json()["verified_scopes"] == ["speaker_catalog"]
    assert response.json()["billing_effect"] == "none"
    assert captured == {
        "action": "ListSpeakers",
        "body": {"ResourceIDs": ["seed-tts-2.0"], "Page": 1, "Limit": 1},
        "timeout": 12,
        "access_key": "test-ak",
    }
    assert "test-ak" not in response.text
    assert "test-sk" not in response.text


def test_cloud_upstream_errors_do_not_echo_provider_body_or_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/settings/doubao-secret", json={"api_key": "do-not-echo"})

    def failed_probe(**kwargs):
        raise doubao_client.DoubaoAPIError(
            "raw upstream error do-not-echo",
            status_code=401,
            logid="safe-log-id",
            body='{"secret":"do-not-echo"}',
        )

    monkeypatch.setattr(doubao_client, "probe_tts_connection", failed_probe)
    response = client.post("/api/settings/cloud-connections/doubao/test")
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "CLOUD_AUTH_FAILED",
        "message": "豆包 TTS 鉴权失败，请检查凭据与接入地址是否匹配",
        "detail": {"upstream_status": 401, "logid": "safe-log-id"},
    }
    assert "do-not-echo" not in response.text
    assert "raw upstream" not in response.text


class _FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _FakeResponse:
    def __init__(self, payload: str):
        self.payload = payload.encode("utf-8")
        self.headers = _FakeHeaders({"X-Tt-Logid": "probe-log"})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


def test_doubao_probe_discards_audio_and_does_not_write_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audio = base64.b64encode(b"tiny-audio").decode("ascii")
    response_payload = json.dumps({"data": audio}, ensure_ascii=False) + json.dumps(
        {"code": 20000000, "message": "ok"}, ensure_ascii=False
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(response_payload)

    monkeypatch.setattr(doubao_client.urllib.request, "urlopen", fake_urlopen)
    before = list(tmp_path.rglob("*"))
    result = doubao_client.probe_tts_connection(
        base_url="https://openspeech.bytedance.com",
        api_key="probe-secret",
        resource_id="seed-tts-test",
        timeout=7,
    )
    after = list(tmp_path.rglob("*"))

    headers = {key.lower(): value for key, value in captured["request"].header_items()}
    body = json.loads(captured["request"].data.decode("utf-8"))
    assert captured["request"].full_url.endswith("/api/v3/tts/unidirectional")
    assert headers["x-api-key"] == "probe-secret"
    assert headers["x-api-resource-id"] == "seed-tts-test"
    assert body["req_params"]["text"] == "测"
    assert result["audio_bytes"] == len(b"tiny-audio")
    assert result["logid"] == "probe-log"
    assert before == after


def test_invalid_cloud_provider_is_rejected_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/settings/cloud-connections/not-a-provider/test")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
