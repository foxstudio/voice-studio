from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import model_catalog, model_install_policy  # noqa: E402


def _verified_snapshot() -> dict:
    return {
        "source_url": "https://example.com/model",
        "install_kind": "managed_model_snapshot",
        "model_license_status": "verified",
        "model_license": "Apache-2.0",
        "model_revision": "0123456789abcdef",
        "download_sources": [{"url": "https://example.com/model/files"}],
    }


def test_verified_pinned_https_snapshot_allows_automatic_download():
    decision = model_install_policy.automatic_download_decision(
        _verified_snapshot()
    )

    assert decision.allowed is True
    assert decision.blockers == ()


@pytest.mark.parametrize(
    ("mutation", "expected_blocker"),
    [
        ({"model_license_status": "unverified"}, "model_license_unverified"),
        ({"model_license": ""}, "model_license_missing"),
        ({"source_url": "http://example.com/model"}, "official_source_not_https"),
        ({"download_sources": []}, "download_source_missing"),
        ({"model_revision": ""}, "model_revision_not_pinned"),
        (
            {
                "license_acceptance_required": True,
                "license_acceptance_id": "",
            },
            "license_acceptance_id_missing",
        ),
    ],
)
def test_automatic_download_fails_closed(mutation, expected_blocker):
    source = {**_verified_snapshot(), **mutation}

    decision = model_install_policy.automatic_download_decision(source)

    assert decision.allowed is False
    assert expected_blocker in decision.blockers


def test_current_catalog_only_allows_verified_runnable_managed_downloads():
    allowed = {
        engine_id
        for engine_id in model_catalog.SOURCES
        if model_catalog.automatic_download_allowed(engine_id)
    }

    assert allowed == {
        "omnivoice",
        "vibevoice-asr-4bit",
        "vibevoice-asr-8bit",
    }


def test_reference_only_resource_cannot_be_automatically_downloaded():
    source = {**_verified_snapshot(), "reference_only": True}

    decision = model_install_policy.automatic_download_decision(source)

    assert decision.allowed is False
    assert "reference_only_resource" in decision.blockers
