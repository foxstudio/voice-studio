from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import model_catalog, omnivoice_model  # noqa: E402


def test_omnivoice_install_api_requires_license_acceptance():
    response = TestClient(app).post(
        "/api/engines/installations/omnivoice/install",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_LICENSE_ACCEPTANCE_REQUIRED"


def test_omnivoice_install_api_starts_pinned_download(monkeypatch):
    captured = {}

    def fake_start(accepted_license_id):
        captured["accepted_license_id"] = accepted_license_id
        return {
            "engine_id": "omnivoice",
            "installed": False,
            "installation_status": "installing",
        }

    monkeypatch.setattr(omnivoice_model, "start_install", fake_start)

    response = TestClient(app).post(
        "/api/engines/installations/omnivoice/install",
        json={"accepted_license_id": omnivoice_model.LICENSE_ACCEPTANCE_ID},
    )

    assert response.status_code == 202
    assert response.json()["installation_status"] == "installing"
    assert captured["accepted_license_id"] == omnivoice_model.LICENSE_ACCEPTANCE_ID


def test_unverified_model_cannot_use_managed_install_api():
    response = TestClient(app).post(
        "/api/engines/installations/indextts-v2/install",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ENGINE_AUTOMATIC_INSTALL_UNSUPPORTED"


def test_install_api_fails_closed_when_catalog_license_is_not_verified(monkeypatch):
    monkeypatch.setattr(
        model_catalog,
        "automatic_download_allowed",
        lambda _engine_id: False,
    )
    monkeypatch.setattr(
        model_catalog,
        "automatic_download_blockers",
        lambda _engine_id: ["model_license_unverified"],
    )

    response = TestClient(app).post(
        "/api/engines/installations/omnivoice/install",
        json={"accepted_license_id": omnivoice_model.LICENSE_ACCEPTANCE_ID},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_LICENSE_UNVERIFIED"
    assert response.json()["error"]["detail"]["blockers"] == [
        "model_license_unverified"
    ]


def test_install_api_reports_non_license_policy_blockers(monkeypatch):
    monkeypatch.setattr(
        model_catalog,
        "automatic_download_allowed",
        lambda _engine_id: False,
    )
    monkeypatch.setattr(
        model_catalog,
        "automatic_download_blockers",
        lambda _engine_id: ["model_checksum_missing"],
    )

    response = TestClient(app).post(
        "/api/engines/installations/omnivoice/install",
        json={"accepted_license_id": omnivoice_model.LICENSE_ACCEPTANCE_ID},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_AUTOMATIC_INSTALL_BLOCKED"
    assert response.json()["error"]["detail"]["blockers"] == [
        "model_checksum_missing"
    ]


def test_catalog_separates_omnivoice_code_and_weight_licenses(tmp_path, monkeypatch):
    incomplete_path = tmp_path / "managed" / "omnivoice"
    incomplete_path.mkdir(parents=True)
    model_path = tmp_path / "external-cache" / "omnivoice"
    model_path.mkdir(parents=True)
    monkeypatch.setattr(
        model_catalog,
        "_candidates",
        lambda _engine_id: [incomplete_path, model_path],
    )
    monkeypatch.setattr(
        model_catalog.omnivoice_model,
        "is_complete_directory",
        lambda path, *, verify_integrity=True: path == model_path,
    )
    monkeypatch.setattr(
        model_catalog.omnivoice_model,
        "installation_status",
        lambda *, verify_integrity=True: {
            "installation_status": "not_installed",
            "integrity": "not_verified",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": omnivoice_model.TOTAL_BYTES,
            "size_bytes": 0,
            "error": None,
        },
    )
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {"healthy": False, "status": "package_missing"},
    )

    entry = model_catalog._entry("omnivoice", model_catalog.SOURCES["omnivoice"])

    assert entry["installed"] is True
    assert entry["preferred_path"] == str(model_path)
    assert entry["automatic_download_supported"] is True
    assert entry["integrity"] == "verified"
    assert entry["code_license"] == "Apache-2.0"
    assert entry["model_license"] == "CC-BY-NC"
    assert entry["model_license_status"] == "verified"
    assert entry["license_acceptance_required"] is True
    assert entry["license_acceptance_id"] == omnivoice_model.LICENSE_ACCEPTANCE_ID
    assert entry["model_revision"] == omnivoice_model.REVISION
