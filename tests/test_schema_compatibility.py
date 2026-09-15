from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_schema_facade_preserves_class_identity():
    from app.models.schemas import GenerateRequest as OldGenerateRequest
    from app.models.schemas import GenerationTask as OldGenerationTask
    from app.schemas.voice_studio import GenerateRequest as NewGenerateRequest
    from app.schemas.voice_studio import GenerationTask as NewGenerationTask

    assert OldGenerateRequest is NewGenerateRequest
    assert OldGenerationTask is NewGenerationTask


def test_schema_package_reexports_common_models():
    from app.models.schemas import EngineManifest as OldEngineManifest
    from app.schemas import EngineManifest as NewEngineManifest

    assert OldEngineManifest is NewEngineManifest


def test_error_facade_preserves_class_identity():
    from app.errors import AppException as NewAppException
    from app.models.exceptions import AppException as OldAppException

    assert OldAppException is NewAppException
    assert NewAppException(status_code=400, code="TEST", message="message").code == "TEST"
