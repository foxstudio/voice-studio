from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.schemas import WebSearchSettings  # noqa: E402
from app.services import database, settings_store, web_search  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    return TestClient(app)


def test_web_search_settings_keep_secret_write_only(tmp_path: Path):
    client = _client(tmp_path)
    saved = client.put(
        "/api/settings/web-search",
        json={
            "enabled": True,
            "provider": "tavily",
            "base_url": "",
            "api_key": " tvly-secret ",
            "max_queries": 2,
            "max_results_per_query": 4,
        },
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "enabled": True,
        "provider": "tavily",
        "base_url": "",
        "api_key_configured": True,
        "max_queries": 2,
        "max_results_per_query": 4,
    }
    assert "tvly-secret" not in saved.text
    assert settings_store.web_search_api_key() == "tvly-secret"

    cleared = client.put(
        "/api/settings/web-search",
        json={
            "enabled": False,
            "provider": "wikipedia",
            "base_url": "",
            "clear_api_key": True,
            "max_queries": 3,
            "max_results_per_query": 5,
        },
    )
    assert cleared.json()["api_key_configured"] is False
    assert settings_store.web_search_api_key() is None


def test_web_search_test_uses_saved_provider(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.put(
        "/api/settings/web-search",
        json={
            "enabled": True,
            "provider": "wikipedia",
            "base_url": "",
            "max_queries": 1,
            "max_results_per_query": 3,
        },
    )
    calls = []

    def fake_search(settings, query, *, api_key=None):
        calls.append((settings.provider, query, api_key))
        return [web_search.SearchResult("Voice Studio", "https://example.com", "result")]

    monkeypatch.setattr(web_search, "search", fake_search)
    response = client.post("/api/settings/web-search/test")
    assert response.status_code == 200
    assert response.json()["result_count"] == 1
    assert calls == [("wikipedia", "Voice Studio subtitle localization", None)]


def test_web_search_settings_reject_searxng_credentials_in_url(tmp_path: Path):
    client = _client(tmp_path)

    response = client.put(
        "/api/settings/web-search",
        json={
            "enabled": True,
            "provider": "searxng",
            "base_url": "https://user:password@example.com?format=json",
            "max_queries": 1,
            "max_results_per_query": 3,
        },
    )

    assert response.status_code == 400
    assert client.get("/api/settings/web-search").json()["base_url"] == ""


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.payload).encode()


def test_wikipedia_result_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        web_search.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(
            {"query": {"search": [{"title": "Seedance", "snippet": "<span>AI</span> video model"}]}}
        ),
    )
    settings = WebSearchSettings(provider="wikipedia", max_results_per_query=3)
    results = web_search.search(settings, "Seedance")
    assert results[0].title == "Seedance"
    assert results[0].snippet == "AI video model"
    assert results[0].url.endswith("/wiki/Seedance")
