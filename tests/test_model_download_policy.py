from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import model_catalog  # noqa: E402


def test_automatic_download_requires_a_verified_weight_license(monkeypatch):
    monkeypatch.setitem(
        model_catalog.SOURCES,
        "unverified-model",
        {
            "install_kind": "managed_model_download",
            "model_license_status": "unverified",
        },
    )
    monkeypatch.setitem(
        model_catalog.SOURCES,
        "missing-license-model",
        {"install_kind": "managed_model_download"},
    )

    assert model_catalog.automatic_download_allowed("unverified-model") is False
    assert model_catalog.automatic_download_allowed("missing-license-model") is False
    assert model_catalog.automatic_download_allowed("unknown-model") is False


def test_verified_license_still_requires_a_managed_install_kind(monkeypatch):
    monkeypatch.setitem(
        model_catalog.SOURCES,
        "manual-model",
        {
            "install_kind": "external_runtime",
            "model_license_status": "verified",
        },
    )

    assert model_catalog.automatic_download_allowed("manual-model") is False
    assert model_catalog.automatic_download_allowed("omnivoice") is True
