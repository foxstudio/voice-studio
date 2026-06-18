from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings
from app.services import database as db
from app.services import settings_store


def _settings_for(tmp_path):
    return AppSettings(
        data_dir=str(tmp_path),
        model_dir=str(tmp_path / "models"),
        voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"),
        export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"),
        cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    )


def test_storage_audit_lists_generation_artifacts(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "config" / "voice_studio.db")

    (tmp_path / "outputs" / "diagnostics").mkdir(parents=True)
    (tmp_path / "outputs" / "diagnostics" / "indextts-v2-diagnosis.wav").write_bytes(b"audio")
    (tmp_path / "cache" / "asr_uploads" / "qwen3-asr-mlx").mkdir(parents=True)
    (tmp_path / "cache" / "asr_uploads" / "qwen3-asr-mlx" / "record.wav").write_bytes(b"source")

    audit = settings_store.storage_audit()
    keys = {item["key"] for item in audit["locations"]}
    flow_names = {item["name"] for item in audit["flows"]}

    assert {"voice_dir", "output_dir", "asr_uploads", "diagnostics", "log_dir"} <= keys
    assert {"自定义音色 ASR", "单条/长文本生成", "引擎诊断"} <= flow_names
    assert audit["total_bytes"] >= len(b"audio") + len(b"source")


def test_cleanup_storage_only_allows_whitelisted_targets(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)

    qwen_dir = tmp_path / "cache" / "qwen-align"
    qwen_dir.mkdir(parents=True)
    (qwen_dir / "worker.log").write_text("log", encoding="utf-8")
    voice_dir = tmp_path / "voices"
    voice_dir.mkdir()
    (voice_dir / "keep.wav").write_bytes(b"voice")

    result = settings_store.cleanup_storage(["qwen_align", "voice_dir"])

    assert result["removed_bytes"] == len("log")
    assert result["skipped"] == ["voice_dir"]
    assert list(qwen_dir.iterdir()) == []
    assert (voice_dir / "keep.wav").exists()


def test_open_storage_location_uses_audited_location_keys(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setattr(settings_store.sys, "platform", "darwin")
    opened: list[list[str]] = []
    monkeypatch.setattr(settings_store.subprocess, "Popen", lambda cmd: opened.append(cmd))

    result = settings_store.open_storage_location("voice_dir")

    assert result["status"] == "opened"
    assert result["key"] == "voice_dir"
    assert result["path"] == str(tmp_path / "voices")
    assert opened == [["open", str(tmp_path / "voices")]]

    try:
        settings_store.open_storage_location("../../../tmp")
    except ValueError as exc:
        assert "Unknown storage location" in str(exc)
    else:
        raise AssertionError("arbitrary paths should not be openable")
