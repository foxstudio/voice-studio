from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import database  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    return TestClient(app)


def test_single_field_patch_preserves_other_settings(tmp_path: Path):
    client = _client(tmp_path)
    initial = client.patch(
        "/api/settings",
        json={"default_language": "en", "theme": "dark", "cloud_enabled": False},
    )
    assert initial.status_code == 200

    updated = client.patch("/api/settings", json={"theme": "light"})

    assert updated.status_code == 200
    assert updated.json()["theme"] == "light"
    assert updated.json()["default_language"] == "en"
    assert updated.json()["cloud_enabled"] is False


def test_patch_can_explicitly_restore_default_and_clear_nullable_value(tmp_path: Path):
    client = _client(tmp_path)
    client.patch(
        "/api/settings",
        json={"theme": "dark", "default_voice_id": "voice-1", "default_language": "en"},
    )

    updated = client.patch(
        "/api/settings",
        json={"theme": "system", "default_voice_id": None},
    )

    assert updated.status_code == 200
    assert updated.json()["theme"] == "system"
    assert updated.json()["default_voice_id"] is None
    assert updated.json()["default_language"] == "en"


def test_invalid_url_returns_safe_serializable_validation_error(tmp_path: Path):
    client = _client(tmp_path)

    response = client.patch(
        "/api/settings",
        json={"doubao_base_url": "https://example.invalid", "api_key": "must-not-echo"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["detail"]
    assert "must-not-echo" not in response.text
    assert all(set(item) == {"type", "loc", "msg"} for item in body["error"]["detail"])


def test_secret_replace_and_clear_are_mutually_exclusive(tmp_path: Path):
    client = _client(tmp_path)

    response = client.patch(
        "/api/settings/doubao-secret",
        json={"api_key": "replacement", "clear": True},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_saving_secret_does_not_enable_cloud_behind_user_choice(tmp_path: Path):
    client = _client(tmp_path)
    client.patch("/api/settings", json={"cloud_enabled": False})

    saved = client.patch(
        "/api/settings/doubao-secret",
        json={"api_key": "replacement"},
    )

    assert saved.status_code == 200
    assert saved.json()["cloud_enabled"] is False
    assert "replacement" not in saved.text


def test_settings_batch_write_rolls_back_as_one_transaction(tmp_path: Path):
    client = _client(tmp_path)
    client.patch("/api/settings", json={"default_language": "zh", "theme": "dark"})
    with database.conn() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_theme_write
            BEFORE INSERT ON settings
            WHEN NEW.key = 'theme'
            BEGIN
                SELECT RAISE(ABORT, 'test rollback');
            END
            """
        )

    with pytest.raises(Exception, match="test rollback"):
        database.apply_settings_changes(
            {
                "default_language": '"en"',
                "theme": '"light"',
            }
        )

    rows = database.get_settings_rows()
    assert rows["default_language"] == '"zh"'
    assert rows["theme"] == '"dark"'
