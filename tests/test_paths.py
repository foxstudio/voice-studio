from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.paths import expand_path  # noqa: E402


def test_expand_path_supports_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_SHARED_ROOT", str(tmp_path / "shared"))

    assert expand_path("${VOICE_STUDIO_SHARED_ROOT}/models") == (
        tmp_path / "shared" / "models"
    )
