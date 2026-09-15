from __future__ import annotations

import asyncio

import pytest

from app.services import video_localization_operations


@pytest.mark.asyncio
async def test_dedicated_worker_owns_start_and_shutdown(
    monkeypatch,
) -> None:
    events: list[str] = []
    stop_event = asyncio.Event()

    def start_worker() -> None:
        events.append("start")
        stop_event.set()

    async def shutdown() -> None:
        events.append("shutdown")

    monkeypatch.setattr(
        video_localization_operations.operation_queue,
        "start_worker",
        start_worker,
    )
    monkeypatch.setattr(
        video_localization_operations.operation_queue,
        "shutdown",
        shutdown,
    )
    monkeypatch.setattr(
        video_localization_operations.settings_store,
        "ensure_directories",
        lambda: events.append("ensure"),
    )

    await video_localization_operations.run_worker(
        stop_event=stop_event,
    )

    assert events == ["ensure", "start", "shutdown"]
