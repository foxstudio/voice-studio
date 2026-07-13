from __future__ import annotations

import os
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

    assert {"assets", "seed_audio_images", "custom_reference_audio", "voice_dir", "output_dir", "asr_uploads", "diagnostics", "log_dir"} <= keys
    assert {"自定义参考音频上传", "自定义音色注册", "自定义音色 ASR", "单条/长文本生成", "引擎诊断"} <= flow_names
    assert audit["total_bytes"] >= len(b"audio") + len(b"source")


def test_ensure_directories_creates_runtime_asset_and_governed_cache_roots(tmp_path):
    settings = _settings_for(tmp_path)

    settings_store.ensure_directories(settings)

    expected = {
        tmp_path / "assets" / "seed-audio" / "images",
        tmp_path / "assets" / "reference-audio" / "custom",
        tmp_path / "cache" / "waveforms",
        tmp_path / "cache" / "qwen-align",
        tmp_path / "cache" / "provider-catalogs",
    }
    assert all(path.is_dir() for path in expected)


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


def test_faster_whisper_candidates_prefer_latest_existing_hf_snapshot(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    hf_home = tmp_path / "hf"
    snapshots = hf_home / "hub" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots"
    older = snapshots / "older-snapshot"
    newer = snapshots / "newer-snapshot"
    older.mkdir(parents=True)
    newer.mkdir()
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    candidates = settings_store.model_candidates("faster-whisper-turbo")

    assert candidates == [newer, tmp_path / "models" / "faster-whisper-turbo"]


def test_qwen_asr_candidates_are_portable_and_discover_hf_cache(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    data_root = tmp_path / "runtime-data"
    configured = tmp_path / "configured-qwen"
    hf_home = tmp_path / "hf"
    snapshot = hf_home / "hub" / "models--mlx-community--Qwen3-ASR-1.7B-8bit" / "snapshots" / "snapshot-a"
    snapshot.mkdir(parents=True)
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_ASR_MODEL_DIR", str(configured))
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data_root))
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    candidates = settings_store.model_candidates("qwen3-asr-mlx")

    assert candidates == [
        configured,
        data_root / "models" / "qwen3-asr-mlx",
        tmp_path / "models" / "qwen3-asr-mlx",
        tmp_path / "models" / "mlx-community_Qwen3-ASR-1.7B-8bit",
        settings_store.PROJECT_ROOT / "models" / "qwen3-asr-mlx",
        settings_store.PROJECT_ROOT / "models" / "mlx-community_Qwen3-ASR-1.7B-8bit",
        snapshot,
    ]
    assert all("/Users/foxmacstudio/Documents/Voxt Modles" not in str(path) for path in candidates)
