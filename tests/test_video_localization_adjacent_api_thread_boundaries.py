from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import project_store  # noqa: E402


@pytest.mark.anyio
async def test_slow_adjacent_project_store_read_does_not_block_health(
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()

    def blocking_list_projects(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(project_store, "list_projects", blocking_list_projects)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        slow_request = asyncio.create_task(client.get("/api/projects"))
        safeguard = threading.Timer(1, release.set)
        safeguard.start()
        try:
            assert await asyncio.to_thread(started.wait, 0.5)
            health = await asyncio.wait_for(
                client.get("/api/health"),
                timeout=0.3,
            )
            assert health.status_code == 200
        finally:
            release.set()
            safeguard.cancel()
            await slow_request
