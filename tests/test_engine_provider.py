from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_provider, engine_registry  # noqa: E402


def test_provider_catalog_matches_engine_registry():
    provider_ids = [provider.engine_id for provider in engine_provider.list_providers()]
    registry_ids = [detail.manifest.engine_id for detail in engine_registry.list_engines()]

    assert provider_ids == registry_ids
    assert "mimo-v2.5-tts" not in provider_ids
    assert "mimo-v2.5-tts-preset" in provider_ids


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

    health = engine_registry.health_check("f5-tts")

    assert health["healthy"] is False
    assert health["status"] == "external_runtime_unconfigured"
    assert "VOICE_STUDIO_F5_TTS_ROOT" in health["detail"]
