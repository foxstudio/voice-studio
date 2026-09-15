from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app as voice_studio_app  # noqa: E402
from app.services.local_api_security import (  # noqa: E402
    LocalBrowserOriginMiddleware,
    is_loopback_origin,
)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://localhost.:5173",
        "http://127.0.0.1:4173",
        "http://[::1]:8000",
        "https://127.0.0.2:9443",
    ],
)
def test_loopback_origins_are_recognized(origin):
    assert is_loopback_origin(origin) is True


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "file:///tmp/index.html",
        "https://example.com",
        "http://localhost.example.com:5173",
        "http://127.0.0.1.example.com",
        "not a url",
    ],
)
def test_non_loopback_origins_are_rejected(origin):
    assert is_loopback_origin(origin) is False


def _test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(LocalBrowserOriginMiddleware)

    @test_app.post("/write")
    async def write():
        return {"written": True}

    @test_app.get("/read")
    async def read():
        return {"read": True}

    return test_app


def test_browser_write_policy_preserves_local_and_native_clients():
    client = TestClient(_test_app())

    assert client.post("/write").status_code == 200
    assert client.post(
        "/write",
        headers={"Origin": "http://127.0.0.1:5173"},
    ).status_code == 200
    assert client.get(
        "/read",
        headers={"Origin": "https://example.com"},
    ).status_code == 200


def test_browser_write_from_remote_origin_is_blocked():
    response = TestClient(_test_app()).post(
        "/write",
        headers={"Origin": "https://example.com"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "UNTRUSTED_BROWSER_ORIGIN"


def test_voice_studio_app_installs_browser_write_protection():
    response = TestClient(voice_studio_app).post(
        "/api/engines/installations/omnivoice/install",
        headers={"Origin": "https://example.com"},
        json={},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "UNTRUSTED_BROWSER_ORIGIN"
