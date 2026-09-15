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

from app.services.frontend_distribution import install_frontend_distribution  # noqa: E402


def _distribution(tmp_path: Path) -> Path:
    distribution = tmp_path / "frontend"
    immutable = distribution / "_app" / "immutable"
    immutable.mkdir(parents=True)
    (distribution / "index.html").write_text("<main>Voice Studio</main>", encoding="utf-8")
    (distribution / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    (immutable / "app.123.js").write_text("export const ready = true;", encoding="utf-8")
    return distribution


def test_frontend_distribution_is_disabled_by_default(tmp_path: Path) -> None:
    app = FastAPI()

    assert install_frontend_distribution(app, environ={}) is None
    assert TestClient(app).get("/").status_code == 404


def test_frontend_distribution_requires_a_complete_build(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="production build"):
        install_frontend_distribution(
            FastAPI(),
            environ={
                "VOICE_STUDIO_SERVE_FRONTEND": "1",
                "VOICE_STUDIO_FRONTEND_DIST": str(tmp_path / "missing"),
            },
        )


def test_frontend_distribution_serves_assets_and_spa_routes(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    install_frontend_distribution(
        app,
        environ={
            "VOICE_STUDIO_SERVE_FRONTEND": "true",
            "VOICE_STUDIO_FRONTEND_DIST": str(distribution),
        },
    )
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/generate").text == "<main>Voice Studio</main>"
    assert client.get("/generate").headers["cache-control"] == "no-cache"
    assert client.get("/robots.txt").text == "User-agent: *"

    asset = client.get("/_app/immutable/app.123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "ready = true" in asset.text


def test_frontend_distribution_does_not_mask_missing_api_or_assets(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    app = FastAPI()
    install_frontend_distribution(
        app,
        environ={
            "VOICE_STUDIO_SERVE_FRONTEND": "yes",
            "VOICE_STUDIO_FRONTEND_DIST": str(distribution),
        },
    )
    client = TestClient(app)

    assert client.get("/api/missing").status_code == 404
    assert client.get("/_app/immutable/missing.js").status_code == 404
    assert client.get("/../outside.txt").status_code == 404
