from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings, HistoryItem  # noqa: E402
from app.services import database as db, history_store, settings_store  # noqa: E402


def test_history_delete_removes_audio_and_waveform_cache(tmp_path, monkeypatch):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    settings = AppSettings(
        data_dir=str(tmp_path),
        voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"),
        export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"),
        cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    )
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    try:
        output = tmp_path / "outputs" / "result.wav"
        output.parent.mkdir()
        output.write_bytes(b"audio")
        waveform = tmp_path / "cache" / "waveforms" / "result-1-2-320.json"
        waveform.parent.mkdir(parents=True)
        waveform.write_text("{}", encoding="utf-8")
        history_store.add(
            HistoryItem(
                result_id="result",
                task_id="task",
                engine_id="indextts-v2",
                input_text="test",
                output_path=str(output),
            )
        )

        history_store.delete("result")

        assert not output.exists()
        assert not waveform.exists()
        assert history_store.get("result") is None
    finally:
        db.set_db_path(original_db)
