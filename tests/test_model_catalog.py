from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import engine_runtime_paths, model_catalog  # noqa: E402


def test_new_install_defaults_models_to_runtime_data_root(monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", "/tmp/voice-studio-data")
    monkeypatch.delenv("VOICE_STUDIO_MODELS_DIR", raising=False)

    assert AppSettings().model_dir == "/tmp/voice-studio-data/models"


def test_engine_root_candidates_are_portable(tmp_path, monkeypatch):
    data_root = tmp_path / "VoiceStudio"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data_root))
    monkeypatch.delenv("VOICE_STUDIO_F5_TTS_ROOT", raising=False)

    candidates = engine_runtime_paths.engine_root_candidates("f5-tts")

    assert candidates[0] == data_root / "engines" / "f5-tts"
    assert candidates[-1] == engine_runtime_paths.PROJECT_ROOT.parent / "tts-engine-lab" / "F5-TTS"


def test_environment_override_wins_for_external_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "external-f5"
    runtime.mkdir()
    monkeypatch.setenv("VOICE_STUDIO_F5_TTS_ROOT", str(runtime))

    assert engine_runtime_paths.resolve_engine_root("f5-tts") == runtime.resolve()


def test_model_catalog_exposes_sources_without_automatic_download():
    entries = {item["engine_id"]: item for item in model_catalog.list_installations()}

    assert entries["indextts-v2"]["source_url"].startswith("https://")
    assert entries["cosyvoice-sft"]["source_url"] == entries["cosyvoice-zero-shot"]["source_url"]
    assert all(item["automatic_download_supported"] is False for item in entries.values())
    assert entries["indextts-v2"]["download_sources"][0]["provider"] == "modelscope"
    assert entries["cosyvoice-sft"]["download_sources"][0]["preferred"] is True
    qwen_source = entries["qwen3-asr-mlx"]["download_sources"][0]
    assert qwen_source["provider"] == "modelscope"
    assert qwen_source["preferred"] is True
    assert "Qwen3-ASR-1.7B-8bit" in qwen_source["url"]
    assert "MLX" in qwen_source["compatibility_note"]
    assert "不静默切换" in entries["qwen3-asr-mlx"]["download_policy"]
