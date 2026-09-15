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
from app.services import settings_preferences
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


def test_first_settings_load_reports_environment_model_root(tmp_path, monkeypatch):
    rows: dict[str, str] = {}
    monkeypatch.setattr(db, "get_settings_rows", lambda: dict(rows))
    monkeypatch.setattr(
        db,
        "apply_settings_changes",
        lambda upserts, deletes=(): (
            rows.update(upserts),
            [rows.pop(key, None) for key in deletes],
        ),
    )
    shared_models = tmp_path / "shared-models"
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", str(shared_models))

    settings = settings_preferences.get()

    assert settings.model_dir_effective == str(shared_models)
    assert settings.model_dir_source == "environment"


def test_storage_audit_lists_generation_artifacts(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "config" / "voice_studio.db")
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)

    (tmp_path / "outputs" / "diagnostics").mkdir(parents=True)
    (tmp_path / "outputs" / "diagnostics" / "indextts-v2-diagnosis.wav").write_bytes(b"audio")
    (tmp_path / "cache" / "asr_uploads" / "qwen3-asr-mlx").mkdir(parents=True)
    (tmp_path / "cache" / "asr_uploads" / "qwen3-asr-mlx" / "record.wav").write_bytes(b"source")
    (tmp_path / "models" / "omnivoice").mkdir(parents=True)
    (tmp_path / "models" / "omnivoice" / "model.safetensors").write_bytes(b"weights")
    proxy = tmp_path / "projects" / "project-a" / "preview" / "source.mp4"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"proxy")

    audit = settings_store.storage_audit()
    keys = {item["key"] for item in audit["locations"]}
    flow_names = {item["name"] for item in audit["flows"]}

    assert {
        "assets",
        "seed_audio_images",
        "custom_reference_audio",
        "voice_dir",
        "output_dir",
        "asr_uploads",
        "diagnostics",
        "video_media_proxy",
        "log_dir",
    } <= keys
    assert {"自定义参考音频上传", "自定义音色注册", "自定义音色 ASR", "单条/长文本生成", "引擎诊断"} <= flow_names
    assert audit["total_bytes"] >= len(b"audio") + len(b"source") + len(b"weights")
    model_location = next(item for item in audit["locations"] if item["key"] == "model_dir")
    assert model_location["size_bytes"] == len(b"weights")


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
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)

    qwen_dir = tmp_path / "cache" / "qwen-align"
    qwen_dir.mkdir(parents=True)
    (qwen_dir / "worker.log").write_text("log", encoding="utf-8")
    waveform_dir = tmp_path / "cache" / "waveforms"
    waveform_dir.mkdir(parents=True)
    (waveform_dir / "timeline.json").write_text("waveform", encoding="utf-8")
    proxy = tmp_path / "projects" / "project-a" / "preview" / "source.mp4"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"proxy")
    source = tmp_path / "projects" / "project-a" / "source" / "source.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    outside_proxy = tmp_path / "outside" / "preview" / "keep.mp4"
    outside_proxy.parent.mkdir(parents=True)
    outside_proxy.write_bytes(b"outside")
    (tmp_path / "projects" / "linked-project").symlink_to(
        outside_proxy.parent.parent,
        target_is_directory=True,
    )
    linked_preview_parent = tmp_path / "projects" / "project-b"
    linked_preview_parent.mkdir()
    (linked_preview_parent / "preview").symlink_to(
        outside_proxy.parent,
        target_is_directory=True,
    )
    voice_dir = tmp_path / "voices"
    voice_dir.mkdir()
    (voice_dir / "keep.wav").write_bytes(b"voice")

    result = settings_store.cleanup_storage(
        ["qwen_align", "waveforms", "video_media_proxy", "voice_dir"]
    )

    assert result["removed_bytes"] == len("log") + len("waveform") + len(b"proxy")
    assert result["skipped"] == ["voice_dir"]
    assert list(qwen_dir.iterdir()) == []
    assert list(waveform_dir.iterdir()) == []
    assert not proxy.exists()
    assert source.read_bytes() == b"source"
    assert outside_proxy.read_bytes() == b"outside"
    assert (voice_dir / "keep.wav").exists()


def test_open_storage_location_uses_audited_location_keys(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)
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


def test_faster_whisper_candidates_prefer_managed_then_latest_hf_snapshot(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)
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

    assert candidates == [tmp_path / "models" / "faster-whisper-turbo", newer]


def test_qwen_asr_candidates_are_portable_and_discover_hf_cache(tmp_path, monkeypatch):
    settings = _settings_for(tmp_path)
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)
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
        tmp_path / "models" / "qwen3-asr-mlx",
        data_root / "models" / "qwen3-asr-mlx",
        tmp_path / "models" / "mlx-community_Qwen3-ASR-1.7B-8bit",
        tmp_path
        / "models"
        / "mlx-audio"
        / "mlx-community_Qwen3-ASR-1.7B-8bit",
        settings_store.PROJECT_ROOT / "models" / "qwen3-asr-mlx",
        settings_store.PROJECT_ROOT / "models" / "mlx-community_Qwen3-ASR-1.7B-8bit",
        snapshot,
    ]
    assert all("Voxt Modles" not in str(path) for path in candidates)
