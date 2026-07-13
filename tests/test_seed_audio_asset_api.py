from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api import seed_audio_assets  # noqa: E402
from app.engines.seed_audio.assets import SeedAudioAssetResolver  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, seed_asset_store, settings_store  # noqa: E402


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(80, 120, 160)).save(output, format=image_format)
    return output.getvalue()


PNG = _image_bytes("PNG")
JPEG = _image_bytes("JPEG")
WEBP = _image_bytes("WEBP")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    old_db_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice-studio.db")
    monkeypatch.setattr(seed_asset_store, "asset_dir", lambda: tmp_path / "managed-seed-assets")

    app = FastAPI()
    app.include_router(seed_audio_assets.router, prefix="/api/seed-audio/assets")

    @app.exception_handler(AppException)
    async def handle_app_exception(_request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail_dict}},
        )

    with TestClient(app) as test_client:
        yield test_client, tmp_path
    database.set_db_path(old_db_path)


@pytest.mark.parametrize(
    ("filename", "content", "expected_format"),
    [("image.png", PNG, "png"), ("image.jpg", JPEG, "jpeg"), ("image.webp", WEBP, "webp")],
)
def test_upload_get_and_resolve_managed_image(client, filename: str, content: bytes, expected_format: str):
    test_client, tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": (filename, content, f"image/{expected_format}")},
        data={"license_status": "authorized"},
    )

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["media_format"] == expected_format
    assert uploaded["size_bytes"] == len(content)
    assert uploaded["source"] == "upload"
    assert uploaded["license_status"] == "authorized"
    assert "path" not in uploaded
    stored = seed_asset_store.get_asset(uploaded["file_id"])
    assert stored is not None
    stored_path = Path(stored.path)
    assert stored_path.read_bytes() == content
    assert stored_path.parent == tmp_path / "managed-seed-assets"

    metadata = test_client.get(f"/api/seed-audio/assets/{uploaded['file_id']}")
    assert metadata.status_code == 200
    assert metadata.json() == uploaded

    reference = SeedAudioAssetResolver().resolve_upload(
        file_id=uploaded["file_id"], media_kind="image", authorized=True
    ).build_reference()
    assert reference.image_data
    assert reference.media_format == expected_format


def test_upload_ignores_path_components_in_original_filename(client):
    test_client, tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("../../outside.png", PNG, "image/png")},
        data={"license_status": "self_voice", "source": "preset"},
    )

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["original_name"] == "outside.png"
    assert uploaded["source"] == "upload"
    assert uploaded["license_status"] == "self_voice"
    assert not (tmp_path / "outside.png").exists()
    stored = seed_asset_store.get_asset(uploaded["file_id"])
    assert stored is not None and Path(stored.path).parent == tmp_path / "managed-seed-assets"


@pytest.mark.parametrize(
    ("filename", "content", "expected_code"),
    [
        ("image.gif", b"GIF89a-data", "SEED_IMAGE_INVALID"),
        ("pretend.png", JPEG, "SEED_IMAGE_FORMAT_MISMATCH"),
        ("empty.png", b"", "SEED_IMAGE_EMPTY"),
    ],
)
def test_rejects_invalid_image_without_leaving_records(client, filename: str, content: bytes, expected_code: str):
    test_client, tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": (filename, content, "application/octet-stream")},
        data={"license_status": "authorized"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
    assert not (tmp_path / "managed-seed-assets").exists()


def test_rejects_image_over_10mb_without_writing_file(client):
    test_client, tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("large.png", PNG + b"x" * (10 * 1024 * 1024), "image/png")},
        data={"license_status": "authorized"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "SEED_IMAGE_TOO_LARGE"
    assert not (tmp_path / "managed-seed-assets").exists()


def test_delete_is_idempotent_and_cannot_delete_voice_store_file(client):
    test_client, tmp_path = client
    upload = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("image.png", PNG, "image/png")},
        data={"license_status": "authorized"},
    ).json()
    stored_path = Path(seed_asset_store.get_asset(upload["file_id"]).path)

    first = test_client.delete(f"/api/seed-audio/assets/{upload['file_id']}")
    second = test_client.delete(f"/api/seed-audio/assets/{upload['file_id']}")
    assert first.json() == {"status": "deleted", "file_id": upload["file_id"], "deleted": True}
    assert second.json() == {"status": "already_absent", "file_id": upload["file_id"], "deleted": False}
    assert not stored_path.exists()

    voice_file = tmp_path / "voice.wav"
    voice_file.write_bytes(b"voice")
    database.upsert(
        "voice_files",
        "voice-file-1",
        {
            "file_id": "voice-file-1",
            "original_name": "voice.wav",
            "path": str(voice_file),
            "mime_type": "audio/wav",
            "duration_ms": 1000,
            "sample_rate": 24000,
            "size_bytes": 5,
            "created_at": "2026-01-01T00:00:00",
        },
        "created_at",
    )
    denied = test_client.delete("/api/seed-audio/assets/voice-file-1")
    assert denied.status_code == 200
    assert denied.json()["deleted"] is False
    assert voice_file.exists()


def test_delete_refuses_tampered_record_outside_managed_directory(client):
    test_client, tmp_path = client
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    record = {
        "file_id": "tampered",
        "asset_type": "seed_audio_image",
        "source": "upload",
        "license_status": "authorized",
        "original_name": "outside.png",
        "path": str(outside),
        "mime_type": "image/png",
        "media_format": "png",
        "size_bytes": len(PNG),
        "created_at": "2026-01-01T00:00:00",
    }
    with database.conn() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS seed_assets (file_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO seed_assets (file_id, data, created_at) VALUES (?, ?, ?)",
            ("tampered", json.dumps(record), record["created_at"]),
        )

    response = test_client.delete("/api/seed-audio/assets/tampered")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSET_PATH_NOT_MANAGED"
    assert outside.exists()
    assert seed_asset_store.get_asset("tampered") is not None


@pytest.mark.parametrize("license_status", ["self_voice", "authorized", "company_authorized", "test_only"])
def test_upload_requires_and_persists_explicit_license(client, license_status: str):
    test_client, _tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("image.png", PNG, "image/png")},
        data={"license_status": license_status},
    )

    assert response.status_code == 201
    assert response.json()["license_status"] == license_status
    stored = seed_asset_store.get_asset(response.json()["file_id"])
    assert stored is not None and stored.license_status == license_status


def test_missing_or_unknown_license_is_rejected(client):
    test_client, _tmp_path = client

    missing = test_client.post(
        "/api/seed-audio/assets/image", files={"file": ("image.png", PNG, "image/png")}
    )
    unknown = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("image.png", PNG, "image/png")},
        data={"license_status": "unknown"},
    )

    assert missing.status_code == 422
    assert unknown.status_code == 422


def test_corrupt_image_with_valid_magic_is_rejected_by_full_decode(client):
    test_client, _tmp_path = client

    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("broken.png", b"\x89PNG\r\n\x1a\ntruncated", "image/png")},
        data={"license_status": "authorized"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SEED_IMAGE_INVALID"


def test_stored_license_and_source_are_authoritative(client):
    test_client, _tmp_path = client
    uploaded = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("image.png", PNG, "image/png")},
        data={"license_status": "test_only", "source": "preset"},
    ).json()
    resolver = SeedAudioAssetResolver()

    with pytest.raises(Exception, match="未授权"):
        resolver.resolve_upload(
            file_id=uploaded["file_id"], media_kind="image", authorized=True, source="upload"
        )

    preset = seed_asset_store.register_preset_image(
        content=PNG,
        original_name="preset.png",
        media_format="png",
        license_status="company_authorized",
    )
    with pytest.raises(Exception, match="来源"):
        resolver.resolve_upload(file_id=preset.file_id, media_kind="image", authorized=True, source="upload")
    managed = resolver.resolve_upload(file_id=preset.file_id, media_kind="image", authorized=False, source="preset")
    assert managed.source == "preset"
    assert managed.license_status == "company_authorized"


def test_orphan_audit_is_conservative_and_cleanup_is_explicit(client):
    _test_client, tmp_path = client
    orphan = seed_asset_store.register_image(
        content=PNG, original_name="orphan.png", media_format="png", license_status="authorized"
    )
    queued = seed_asset_store.register_image(
        content=PNG, original_name="queued.png", media_format="png", license_status="authorized"
    )
    failed = seed_asset_store.register_image(
        content=PNG, original_name="failed.png", media_format="png", license_status="authorized"
    )
    historical = seed_asset_store.register_image(
        content=PNG, original_name="history.png", media_format="png", license_status="authorized"
    )
    preset = seed_asset_store.register_preset_image(
        content=PNG, original_name="preset.png", media_format="png", license_status="authorized"
    )
    future = datetime.now() + timedelta(days=2)
    task_rows = [
        {"status": "queued", "parameters": {"input_assets": [{"file_id": queued.file_id}]}},
        {"status": "failed", "parameters": {"input_assets": [{"file_id": failed.file_id}]}},
    ]
    history_rows = [{"parameters": {"input_assets": [{"source_file_id": historical.file_id}]}}]

    candidates = seed_asset_store.audit_orphaned_assets(
        ttl_seconds=3600, now=future, task_rows=task_rows, history_rows=history_rows
    )

    assert [asset.file_id for asset in candidates] == [orphan.file_id]
    assert Path(orphan.path).exists()
    deleted = seed_asset_store.cleanup_orphaned_assets(
        ttl_seconds=3600, now=future, task_rows=task_rows, history_rows=history_rows
    )
    assert deleted == [orphan.file_id]
    assert not Path(orphan.path).exists()
    assert all(Path(asset.path).exists() for asset in (queued, failed, historical, preset))
    assert (tmp_path / "managed-seed-assets").exists()


def test_api_refuses_delete_while_history_references_asset(client):
    test_client, _tmp_path = client
    uploaded = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": ("image.png", PNG, "image/png")},
        data={"license_status": "authorized"},
    ).json()
    database.upsert(
        "history",
        "result-1",
        {"result_id": "result-1", "created_at": "2026-01-01T00:00:00", "parameters": {"file_id": uploaded["file_id"]}},
        "created_at",
    )

    response = test_client.delete(f"/api/seed-audio/assets/{uploaded['file_id']}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEED_ASSET_IN_USE"
    assert seed_asset_store.get_asset(uploaded["file_id"]) is not None


def test_public_delete_cannot_remove_internal_preset(client):
    test_client, _tmp_path = client
    preset = seed_asset_store.register_preset_image(
        content=PNG, original_name="preset.png", media_format="png", license_status="authorized"
    )

    response = test_client.delete(f"/api/seed-audio/assets/{preset.file_id}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SEED_PRESET_DELETE_FORBIDDEN"
    assert seed_asset_store.get_asset(preset.file_id) is not None


def test_original_name_is_bounded(client):
    test_client, _tmp_path = client
    response = test_client.post(
        "/api/seed-audio/assets/image",
        files={"file": (f"{'x' * 300}.png", PNG, "image/png")},
        data={"license_status": "authorized"},
    )

    assert response.status_code == 201
    assert len(response.json()["original_name"]) == 255


def test_main_app_registers_seed_asset_routes():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/seed-audio/assets/image" in paths
    assert "/api/seed-audio/assets/{file_id}" in paths


def test_default_asset_dir_uses_runtime_assets_layout(tmp_path, monkeypatch):
    settings = AppSettings(data_dir=str(tmp_path))
    monkeypatch.setattr(settings_store, "get", lambda: settings)

    assert seed_asset_store.asset_dir() == tmp_path / "assets" / "seed-audio" / "images"
    assert seed_asset_store.legacy_asset_dir() == tmp_path / "seed_audio" / "assets"


def test_legacy_asset_is_migrated_on_read_and_remains_deletable(client, monkeypatch):
    _test_client, tmp_path = client
    current_root = tmp_path / "managed-seed-assets"
    legacy_root = tmp_path / "legacy-seed-assets"
    monkeypatch.setattr(seed_asset_store, "legacy_asset_dir", lambda: legacy_root)
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy-image.png"
    legacy_path.write_bytes(PNG)
    record = {
        "file_id": "legacy-image",
        "asset_type": "seed_audio_image",
        "source": "upload",
        "license_status": "authorized",
        "original_name": "legacy.png",
        "path": str(legacy_path),
        "mime_type": "image/png",
        "media_format": "png",
        "size_bytes": len(PNG),
        "created_at": "2026-01-01T00:00:00",
    }
    with database.conn() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS seed_assets (file_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO seed_assets (file_id, data, created_at) VALUES (?, ?, ?)",
            (record["file_id"], json.dumps(record), record["created_at"]),
        )

    migrated = seed_asset_store.get_asset(record["file_id"])

    assert migrated is not None
    assert Path(migrated.path) == current_root / "legacy-image.png"
    assert Path(migrated.path).read_bytes() == PNG
    assert not legacy_path.exists()
    assert seed_asset_store.delete_asset(record["file_id"]) is True
    assert not Path(migrated.path).exists()


def test_register_refuses_symlinked_asset_root(client, monkeypatch):
    _test_client, tmp_path = client
    outside = tmp_path / "outside-assets"
    outside.mkdir()
    linked = tmp_path / "linked-assets"
    linked.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(seed_asset_store, "asset_dir", lambda: linked)

    with pytest.raises(seed_asset_store.SeedAssetStoreError, match="符号链接"):
        seed_asset_store.register_image(
            content=PNG,
            original_name="image.png",
            media_format="png",
            license_status="authorized",
        )

    assert list(outside.iterdir()) == []


def test_delete_refuses_symlinked_legacy_root(client, monkeypatch):
    _test_client, tmp_path = client
    outside = tmp_path / "outside-legacy"
    outside.mkdir()
    linked = tmp_path / "linked-legacy"
    linked.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(seed_asset_store, "legacy_asset_dir", lambda: linked)
    legacy_path = outside / "linked-legacy.png"
    legacy_path.write_bytes(PNG)
    record = {
        "file_id": "linked-legacy",
        "asset_type": "seed_audio_image",
        "source": "upload",
        "license_status": "authorized",
        "original_name": "legacy.png",
        "path": str(linked / legacy_path.name),
        "mime_type": "image/png",
        "media_format": "png",
        "size_bytes": len(PNG),
        "created_at": "2026-01-01T00:00:00",
    }
    with database.conn() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS seed_assets (file_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO seed_assets (file_id, data, created_at) VALUES (?, ?, ?)",
            (record["file_id"], json.dumps(record), record["created_at"]),
        )

    with pytest.raises(seed_asset_store.SeedAssetStoreError, match="受管理目录以外"):
        seed_asset_store.delete_asset(record["file_id"])

    assert legacy_path.read_bytes() == PNG
