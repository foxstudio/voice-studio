from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import stem_separation_engine  # noqa: E402
from app.services import stem_separation_tasks  # noqa: E402


def _wav_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((4800, 2), dtype=np.float32), 48000)
    return source.read_bytes()


def _wait(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        payload = client.get(f"/api/audio-tools/stem-tasks/{task_id}").json()
        if payload["status"] in {"success", "failed", "cancelled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("stem task did not complete")


def test_stem_task_upload_download_and_cleanup(tmp_path: Path, monkeypatch):
    def fake_separate(source, vocals, background, **kwargs):
        audio, sample_rate = sf.read(source, always_2d=True)
        sf.write(vocals, audio, sample_rate)
        sf.write(background, audio, sample_rate)
        return {"engine_id": stem_separation_engine.ENGINE_ID}

    monkeypatch.setattr(stem_separation_engine, "separate", fake_separate)
    with TestClient(app) as client:
        created = client.post(
            "/api/audio-tools/stem-tasks",
            files={"file": ("source.wav", _wav_bytes(tmp_path), "audio/wav")},
        )
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        result = _wait(client, task_id)
        assert result["status"] == "success"
        assert result["vocals_ready"] is True
        assert result["background_ready"] is True
        assert "source_path" not in result

        vocals = client.get(
            f"/api/audio-tools/stem-tasks/{task_id}/audio/vocals"
        )
        background = client.get(
            f"/api/audio-tools/stem-tasks/{task_id}/audio/background"
        )
        assert vocals.status_code == 200 and vocals.content
        assert background.status_code == 200 and background.content

        deleted = client.delete(f"/api/audio-tools/stem-tasks/{task_id}")
        assert deleted.status_code == 200
        assert client.get(
            f"/api/audio-tools/stem-tasks/{task_id}"
        ).status_code == 404


def test_stem_task_rejects_unsupported_upload(tmp_path: Path):
    with TestClient(app) as client:
        response = client.post(
            "/api/audio-tools/stem-tasks",
            files={"file": ("source.mp3", b"not-audio", "audio/mpeg")},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "STEM_SOURCE_FORMAT_UNSUPPORTED"


def test_orphaned_active_task_becomes_failed_and_deletable():
    task_id = "orphaned-stem-task"
    stem_separation_tasks._write_task(
        {
            "task_id": task_id,
            "filename": "source.wav",
            "status": "running",
            "engine_id": stem_separation_engine.ENGINE_ID,
            "runtime_id": "old-runtime",
        }
    )
    task = stem_separation_tasks.get_task(task_id)
    assert task is not None
    assert task.status.value == "failed"
    assert "restarted" in (task.error_message or "")
    assert stem_separation_tasks.delete_task(task_id) is True
