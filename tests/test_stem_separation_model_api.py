from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import stem_separation_model  # noqa: E402


def test_unverified_bs_roformer_cannot_use_managed_install_api():
    response = TestClient(app).post(
        f"/api/engines/installations/{stem_separation_model.ENGINE_ID}/install"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_LICENSE_UNVERIFIED"


def test_other_models_cannot_use_managed_install_api():
    response = TestClient(app).post(
        "/api/engines/installations/indextts-v2/install"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "ENGINE_AUTOMATIC_INSTALL_UNSUPPORTED"
    )
