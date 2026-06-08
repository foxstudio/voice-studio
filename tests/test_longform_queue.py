from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import longform_queue  # noqa: E402


def test_longform_generate_endpoint_creates_parent_task(monkeypatch):
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda task_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/longform/generate",
        json={
            "generate_request": {
                "text": "第一段内容。第二段内容。",
                "engine_id": "indextts-v2",
                "voice_id": "voice-a",
                "language": "zh",
                "output_format": "mp3",
            },
            "segments": [
                {"index": 1, "text": "第一段内容。", "char_count": 5, "segment_reason": "sentence_boundary"},
                {"index": 2, "text": "第二段内容。", "char_count": 5, "segment_reason": "sentence_boundary"},
            ],
            "verify_enabled": True,
            "merge_enabled": True,
            "max_retries": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["engine_id"] == "indextts-v2"
    assert len(body["segments"]) == 2
    assert body["segments"][0]["status"] == "pending"
    assert body["verify_enabled"] is True
    assert body["merge_enabled"] is True


def test_longform_list_endpoint_returns_tasks(monkeypatch):
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda task_id: None)
    client = TestClient(app)
    created = client.post(
        "/api/longform/generate",
        json={
            "generate_request": {
                "text": "只生成一个短段落。",
                "engine_id": "indextts-v2",
                "voice_id": "voice-a",
                "language": "zh",
                "output_format": "wav",
            },
            "segments": [{"index": 1, "text": "只生成一个短段落。", "char_count": 9, "segment_reason": "direct_text"}],
            "verify_enabled": False,
            "merge_enabled": False,
        },
    ).json()

    response = client.get("/api/longform")

    assert response.status_code == 200
    assert any(item["longform_task_id"] == created["longform_task_id"] for item in response.json())
