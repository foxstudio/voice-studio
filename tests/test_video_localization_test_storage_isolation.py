from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database


def test_pytest_storage_is_outside_product_and_repository_roots() -> None:
    repository_root = ROOT
    product_root = (Path.home() / "VoiceStudio").resolve()
    data_root = Path(os.environ["VOICE_STUDIO_DATA_DIR"]).resolve()

    assert not data_root.is_relative_to(repository_root)
    assert not data_root.is_relative_to(product_root)

    for environment_name in (
        "VOICE_STUDIO_DB_PATH",
        "VOICE_STUDIO_MODELS_DIR",
        "VOICE_STUDIO_VOICES_DIR",
        "VOICE_STUDIO_OUTPUTS_DIR",
        "VOICE_STUDIO_EXPORTS_DIR",
        "VOICE_STUDIO_PROJECTS_DIR",
        "VOICE_STUDIO_CACHE_DIR",
        "VOICE_STUDIO_LOGS_DIR",
    ):
        configured = Path(
            os.environ[environment_name]
        ).resolve()
        assert configured.is_relative_to(data_root)


def test_default_database_is_the_single_config_database() -> None:
    expected = (
        Path(
            os.environ["VOICE_STUDIO_DATA_DIR"]
        ) / "config" / "voice_studio.db"
    ).resolve()

    assert Path(
        os.environ["VOICE_STUDIO_DB_PATH"]
    ).resolve() == expected
    assert database._default_db_path() == expected


def test_pytest_tmp_path_uses_the_session_storage(
    tmp_path: Path,
) -> None:
    data_root = Path(
        os.environ["VOICE_STUDIO_DATA_DIR"]
    ).resolve()

    assert tmp_path.resolve().is_relative_to(data_root)
