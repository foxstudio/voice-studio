from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import confucius4_paths, engine_runtime_paths, model_catalog  # noqa: E402
from app.services.engine_manifests import ENGINES  # noqa: E402


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


def test_confucius_default_paths_follow_the_current_data_root(tmp_path, monkeypatch):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.delenv("VOICE_STUDIO_CONFUCIUS4_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_CONFUCIUS4_MLX_AUDIO_ROOT", raising=False)

    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(first_root))
    assert confucius4_paths.model_candidates()[0] == (
        first_root / "models" / "confucius4-mlx-int8"
    )
    assert confucius4_paths.runtime_root() == (
        first_root / "engines" / "mlx-audio-confucius4"
    )

    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(second_root))
    assert confucius4_paths.model_candidates()[0] == (
        second_root / "models" / "confucius4-mlx-int8"
    )
    assert confucius4_paths.runtime_root() == (
        second_root / "engines" / "mlx-audio-confucius4"
    )


def test_speaker_model_catalog_uses_current_managed_paths(tmp_path, monkeypatch):
    settings = AppSettings(
        data_dir=str(tmp_path / "data"),
        model_dir=str(tmp_path / "managed-models"),
    )
    monkeypatch.setattr(model_catalog.settings_store, "get", lambda: settings)
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)
    monkeypatch.delenv("VOICE_STUDIO_MOSS_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_MOSS_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_CAMPPLUS_ROOT", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_CAMPPLUS_MODEL", raising=False)

    assert model_catalog._candidates("moss-transcribe-diarize-mlx") == [
        tmp_path / "managed-models" / "moss-transcribe-diarize-8bit",
        tmp_path / "data" / "engines" / "moss-transcribe-diarize",
    ]
    assert model_catalog._candidates("campplus-modelscope") == [
        tmp_path / "managed-models" / "campplus-speaker-verifier",
        tmp_path / "data" / "engines" / "campplus-speaker-verifier",
    ]


def test_model_catalog_blocks_unverified_bs_roformer_download():
    entries = {item["engine_id"]: item for item in model_catalog.list_installations()}

    assert entries["indextts-v2"]["source_url"].startswith("https://")
    assert entries["cosyvoice-sft"]["source_url"] == "https://github.com/QwenAudio/CosyVoice"
    assert entries["cosyvoice-sft"]["source_url"] == entries["cosyvoice-zero-shot"]["source_url"]
    managed = entries["bs-roformer-viperx-1297"]
    assert managed["automatic_download_supported"] is False
    assert managed["model_license_status"] == "unverified"
    assert managed["model_sha256"]
    assert managed["architecture"] == "BS-RoFormer (MDXC)"
    managed_downloads = {
        "omnivoice",
        "vibevoice-asr-4bit",
        "vibevoice-asr-8bit",
    }
    assert all(
        item["automatic_download_supported"] is False
        for engine_id, item in entries.items()
        if engine_id not in managed_downloads
    )
    assert all(entries[engine_id]["automatic_download_supported"] for engine_id in managed_downloads)
    assert entries["omnivoice"]["automatic_download_supported"] is True
    assert entries["omnivoice"]["model_license"] == "CC-BY-NC"
    assert entries["omnivoice"]["model_license_status"] == "verified"
    assert entries["omnivoice"]["license_acceptance_required"] is True
    assert entries["indextts-v2"]["download_sources"][0]["provider"] == "modelscope"
    assert any(
        source["provider"] == "huggingface"
        and "IndexTTS-2" in source["url"]
        for source in entries["indextts-v2"]["download_sources"]
    )
    assert any("MaskGCT" in source["url"] for source in entries["indextts-v2"]["download_sources"])
    assert entries["cosyvoice-sft"]["download_sources"][0]["preferred"] is True
    qwen_source = entries["qwen3-asr-mlx"]["download_sources"][0]
    assert qwen_source["provider"] == "modelscope"
    assert qwen_source["preferred"] is True
    assert "Qwen3-ASR-1.7B-8bit" in qwen_source["url"]
    assert "MLX" in qwen_source["compatibility_note"]
    assert any(
        source["provider"] == "huggingface"
        for source in entries["qwen3-asr-mlx"]["download_sources"]
    )
    assert "不静默切换" in entries["qwen3-asr-mlx"]["download_policy"]
    assert entries["vibevoice-asr-4bit"]["runtime_engine_id"] == "vibevoice-asr-mlx-4bit"
    assert entries["vibevoice-asr-8bit"]["runtime_engine_id"] == "vibevoice-asr-mlx-8bit"
    assert entries["vibevoice-asr-official"]["runtime_engine_id"] is None
    assert entries["vibevoice-asr-official"]["reference_only"] is True
    assert entries["vibevoice-asr-official"]["automatic_download_supported"] is False
    assert entries["qwen3-forced-aligner"]["category"] == "localization_model"
    assert entries["semantic-alignment-labse"]["category"] == "localization_model"
    assert entries["confucius4-mlx-int8"]["source_url"] == (
        "https://huggingface.co/mlx-community/Confucius4-TTS-mlx-int8"
    )
    for engine_id in {
        "emotivoice",
        "confucius4-mlx-int8",
        "qwen3-tts-mlx-0.6b",
        "f5-tts",
        "faster-whisper-turbo",
    }:
        assert entries[engine_id]["download_sources"]
    assert entries["faster-whisper-turbo"]["download_sources"][0]["url"] == (
        "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    )


def test_catalog_listing_uses_fast_managed_model_checks(tmp_path, monkeypatch):
    calls: list[tuple[str, bool]] = []

    def vibe_status(variant_id, *, verify_integrity=True):
        calls.append((f"vibe:{variant_id}", verify_integrity))
        return {
            "preferred_path": str(tmp_path / variant_id),
            "installed": False,
            "installation_status": "not_installed",
        }

    def vibe_health(provider_id, *, verify_integrity=True):
        calls.append((f"vibe-health:{provider_id}", verify_integrity))
        return {"healthy": False, "status": "model_missing"}

    def stem_status(*, verify_integrity=True):
        calls.append(("stem", verify_integrity))
        return {
            "preferred_path": str(tmp_path / "stem"),
            "installed": False,
            "installation_status": "not_installed",
        }

    def omnivoice_status(*, verify_integrity=True):
        calls.append(("omnivoice", verify_integrity))
        return {
            "installation_status": "not_installed",
            "integrity": "not_verified",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "size_bytes": 0,
            "error": None,
        }

    monkeypatch.setattr(model_catalog.vibevoice_model, "installation_status", vibe_status)
    monkeypatch.setattr(model_catalog.vibevoice_asr, "model_health", vibe_health)
    monkeypatch.setattr(model_catalog.stem_separation_model, "installation_status", stem_status)
    monkeypatch.setattr(model_catalog.omnivoice_model, "installation_status", omnivoice_status)
    monkeypatch.setattr(model_catalog, "_candidates", lambda engine_id: [])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda engine_id: {"healthy": False, "status": "model_missing"},
    )

    model_catalog._entry("vibevoice-asr-4bit", model_catalog.SOURCES["vibevoice-asr-4bit"])
    model_catalog._entry(
        model_catalog.stem_separation_model.ENGINE_ID,
        model_catalog.SOURCES[model_catalog.stem_separation_model.ENGINE_ID],
    )
    model_catalog._entry("omnivoice", model_catalog.SOURCES["omnivoice"])

    assert calls == [
        ("vibe:4bit", False),
        ("vibe-health:vibevoice-asr-mlx-4bit", False),
        ("stem", False),
        ("omnivoice", False),
    ]


def test_every_local_runtime_has_model_sources_and_download_links():
    local_engine_ids = {
        engine_id
        for engine_id, detail in ENGINES.items()
        if "local_inference" in detail.manifest.capabilities
    }
    catalog_runtime_ids = {
        source.get("runtime_engine_id", catalog_id)
        for catalog_id, source in model_catalog.SOURCES.items()
        if not source.get("reference_only")
    }

    assert local_engine_ids <= catalog_runtime_ids
    for source in model_catalog.SOURCES.values():
        assert source["source_url"].startswith("https://")
        assert source.get("download_sources")
        assert all(item["url"].startswith("https://") for item in source["download_sources"])


def test_every_cloud_engine_has_official_documentation():
    cloud_manifests = [
        detail.manifest
        for detail in ENGINES.values()
        if "cloud_api" in detail.manifest.capabilities
    ]

    assert cloud_manifests
    assert all(manifest.documentation_url for manifest in cloud_manifests)
    assert all(manifest.documentation_label for manifest in cloud_manifests)
