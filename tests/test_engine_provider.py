from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_compatibility, engine_provider, engine_registry, runtime_capabilities  # noqa: E402


def test_provider_catalog_matches_engine_registry():
    provider_ids = [provider.engine_id for provider in engine_provider.list_providers()]
    registry_ids = [detail.manifest.engine_id for detail in engine_registry.list_engines()]

    assert provider_ids == registry_ids
    assert "mimo-v2.5-tts" not in provider_ids
    assert "mimo-v2.5-tts-preset" in provider_ids
    assert "faster-whisper-turbo" in provider_ids


def test_provider_resolves_legacy_mimo_alias():
    provider = engine_provider.get_provider("mimo-v2.5-tts")

    assert provider is not None
    assert provider.engine_id == "mimo-v2.5-tts-preset"
    assert provider.detail.manifest.engine_id == "mimo-v2.5-tts-preset"
    assert provider.runner_kind == "cloud"
    assert provider.requires_idempotency_marker is True


def test_unknown_provider_returns_none():
    assert engine_provider.get_provider("missing-engine") is None
    assert engine_provider.get_engine_detail("missing-engine") is None


def test_external_engine_health_without_root_env_is_structured(monkeypatch):
    monkeypatch.delenv("VOICE_STUDIO_F5_TTS_ROOT", raising=False)
    monkeypatch.setitem(engine_registry.engine_health.DEFAULT_EXTERNAL_ROOTS, "f5-tts", Path("/missing/f5-tts"))

    health = engine_registry.health_check("f5-tts")

    assert health["healthy"] is False
    assert health["status"] == "external_runtime_unconfigured"
    assert "VOICE_STUDIO_F5_TTS_ROOT" in health["detail"]


def test_provider_exposes_platform_incompatibility_before_model_health(monkeypatch):
    snapshot = runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system="windows",
        architecture="x86_64",
        python_version="3.12.0",
        available_devices=("cuda", "cpu"),
        preferred_device="cuda",
        frameworks={"mlx": False, "torch": True},
        optional_capabilities={},
    )
    monkeypatch.setattr(
        engine_compatibility.runtime_capabilities,
        "current_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        engine_provider.engine_health,
        "health_check",
        lambda _engine_id: (_ for _ in ()).throw(
            AssertionError("model health must not run on an incompatible platform")
        ),
    )

    provider = engine_provider.get_provider("indextts-v2")
    assert provider is not None

    detail = provider.detail
    health = provider.health_check()

    assert detail.compatibility.compatible is False
    assert detail.compatibility.reason_code == "platform_unsupported"
    assert health["healthy"] is False
    assert health["status"] == "platform_unsupported"
    assert "Windows" in health["detail"]


def test_provider_keeps_windows_compatible_engine_available_for_runtime_health(monkeypatch):
    snapshot = runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system="windows",
        architecture="x86_64",
        python_version="3.12.0",
        available_devices=("cpu",),
        preferred_device="cpu",
        frameworks={"mlx": False, "torch": True},
        optional_capabilities={},
    )
    monkeypatch.setattr(
        engine_compatibility.runtime_capabilities,
        "current_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        engine_provider.engine_health,
        "health_check",
        lambda engine_id: {"healthy": True, "status": "ok", "engine_id": engine_id},
    )

    provider = engine_provider.get_provider("f5-tts")
    assert provider is not None

    assert provider.detail.compatibility.compatible is True
    assert provider.health_check()["healthy"] is True
