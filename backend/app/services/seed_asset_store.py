"""Persistent store for Seed Audio inputs that are not voice-library audio."""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterable
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.voice_studio import new_id, now_iso
from app.services import database as db, settings_store
from app.services.paths import expand_path

MAX_IMAGE_BYTES = 10 * 1024 * 1024

class SeedAssetStoreError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SeedImageAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file_id: str = Field(min_length=1, max_length=128)
    asset_type: Literal["seed_audio_image"] = "seed_audio_image"
    source: Literal["upload", "preset"]
    license_status: Literal["self_voice", "authorized", "company_authorized", "test_only"]
    original_name: str = Field(min_length=1, max_length=255)
    path: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    media_format: Literal["jpeg", "png", "webp"]
    size_bytes: int
    created_at: str

    def public_metadata(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"path"})


def asset_dir() -> Path:
    return expand_path(settings_store.get().data_dir) / "seed_audio" / "assets"


def register_image(
    *,
    content: bytes,
    original_name: str,
    media_format: Literal["jpeg", "png", "webp"],
    license_status: Literal["self_voice", "authorized", "company_authorized", "test_only"],
) -> SeedImageAsset:
    return _register_image(
        content=content,
        original_name=original_name,
        media_format=media_format,
        license_status=license_status,
        source="upload",
    )


def register_preset_image(
    *,
    content: bytes,
    original_name: str,
    media_format: Literal["jpeg", "png", "webp"],
    license_status: Literal["self_voice", "authorized", "company_authorized", "test_only"],
) -> SeedImageAsset:
    """Internal-only preset registration; no HTTP route exposes this operation."""

    return _register_image(
        content=content,
        original_name=original_name,
        media_format=media_format,
        license_status=license_status,
        source="preset",
    )


def _register_image(
    *,
    content: bytes,
    original_name: str,
    media_format: Literal["jpeg", "png", "webp"],
    license_status: Literal["self_voice", "authorized", "company_authorized", "test_only"],
    source: Literal["upload", "preset"],
) -> SeedImageAsset:
    if not content:
        raise SeedAssetStoreError("SEED_IMAGE_EMPTY", "参考图片不能为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise SeedAssetStoreError("SEED_IMAGE_TOO_LARGE", "参考图片不能超过 10 MB")
    if decode_image_format(content) != media_format:
        raise SeedAssetStoreError("SEED_IMAGE_FORMAT_MISMATCH", "图片内容与登记格式不一致")
    root = asset_dir()
    root.mkdir(parents=True, exist_ok=True)
    extension = "jpg" if media_format == "jpeg" else media_format
    mime_type = "image/jpeg" if media_format == "jpeg" else f"image/{media_format}"

    for _attempt in range(5):
        file_id = new_id()
        path = root / f"{file_id}.{extension}"
        try:
            with path.open("xb") as output:
                output.write(content)
        except FileExistsError:
            continue
        asset = SeedImageAsset(
            file_id=file_id,
            source=source,
            license_status=license_status,
            original_name=_safe_name(original_name, fallback=f"image.{extension}"),
            path=str(path),
            mime_type=mime_type,
            media_format=media_format,
            size_bytes=len(content),
            created_at=now_iso(),
        )
        try:
            _upsert(asset)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return asset
    raise SeedAssetStoreError("ASSET_ID_COLLISION", "无法分配素材标识，请重试")


def get_asset(file_id: str) -> SeedImageAsset | None:
    _validate_id(file_id)
    _ensure_table()
    with db.conn() as connection:
        row = connection.execute("SELECT data FROM seed_assets WHERE file_id = ?", (file_id,)).fetchone()
    return SeedImageAsset(**json.loads(row["data"])) if row else None


def delete_asset(file_id: str) -> bool:
    """Delete only records owned by this store; missing IDs are idempotent."""

    asset = get_asset(file_id)
    if asset is None:
        return False
    path = Path(asset.path).expanduser().resolve(strict=False)
    root = asset_dir().expanduser().resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SeedAssetStoreError("ASSET_PATH_NOT_MANAGED", "拒绝删除受管理目录以外的文件") from exc
    if path.name != f"{asset.file_id}.{'jpg' if asset.media_format == 'jpeg' else asset.media_format}":
        raise SeedAssetStoreError("ASSET_PATH_NOT_MANAGED", "素材路径与标识不匹配")

    path.unlink(missing_ok=True)
    with db.conn() as connection:
        connection.execute("DELETE FROM seed_assets WHERE file_id = ?", (file_id,))
    return True


def list_assets() -> list[SeedImageAsset]:
    _ensure_table()
    with db.conn() as connection:
        rows = connection.execute("SELECT data FROM seed_assets ORDER BY created_at DESC").fetchall()
    return [SeedImageAsset(**json.loads(row["data"])) for row in rows]


def referenced_file_ids(
    *,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
) -> set[str]:
    """Collect IDs referenced by active work or durable history parameters."""

    if task_rows is None:
        task_rows = db.list_all("tasks", "created_at", limit=-1)
    if history_rows is None:
        history_rows = db.list_all("history", "created_at", limit=-1)
    active_statuses = {"pending", "queued", "running", "postprocessing", "retrying"}
    active_tasks = [row for row in task_rows if str(row.get("status", "")) in active_statuses]
    referenced: set[str] = set()
    for row in [*active_tasks, *history_rows]:
        _collect_file_ids(row, referenced)
    return referenced


def audit_orphaned_assets(
    *,
    ttl_seconds: float,
    now: datetime | None = None,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
) -> list[SeedImageAsset]:
    """List old unreferenced uploads. Presets are intentionally never candidates."""

    if ttl_seconds < 0:
        raise SeedAssetStoreError("INVALID_ASSET_TTL", "TTL 不能为负数")
    cutoff = (now or datetime.now()) - timedelta(seconds=ttl_seconds)
    referenced = referenced_file_ids(task_rows=task_rows, history_rows=history_rows)
    candidates: list[SeedImageAsset] = []
    for asset in list_assets():
        try:
            created_at = datetime.fromisoformat(asset.created_at)
        except ValueError:
            continue
        if asset.source == "upload" and created_at <= cutoff and asset.file_id not in referenced:
            candidates.append(asset)
    return candidates


def cleanup_orphaned_assets(
    *,
    ttl_seconds: float,
    now: datetime | None = None,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
) -> list[str]:
    """Explicit cleanup entrypoint; callers must invoke it deliberately."""

    materialized_tasks = list(task_rows) if task_rows is not None else None
    materialized_history = list(history_rows) if history_rows is not None else None
    candidates = audit_orphaned_assets(
        ttl_seconds=ttl_seconds,
        now=now,
        task_rows=materialized_tasks,
        history_rows=materialized_history,
    )
    deleted: list[str] = []
    for asset in candidates:
        current_references = referenced_file_ids(task_rows=materialized_tasks, history_rows=materialized_history)
        if asset.file_id in current_references:
            continue
        if delete_asset(asset.file_id):
            deleted.append(asset.file_id)
    return deleted


def is_asset_referenced(file_id: str) -> bool:
    _validate_id(file_id)
    return file_id in referenced_file_ids()


def decode_image_format(content: bytes) -> Literal["jpeg", "png", "webp"]:
    """Fully decode the exact bytes that will be persisted and later encoded."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                raw_format = str(image.format or "").upper()
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError) as exc:
        raise SeedAssetStoreError("SEED_IMAGE_INVALID", "只支持完整有效的 JPEG、PNG 或 WebP 图片") from exc
    detected = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}.get(raw_format)
    if detected is None:
        raise SeedAssetStoreError("SEED_IMAGE_INVALID", "只支持完整有效的 JPEG、PNG 或 WebP 图片")
    return detected


def _upsert(asset: SeedImageAsset) -> None:
    _ensure_table()
    with db.conn() as connection:
        connection.execute(
            "INSERT INTO seed_assets (file_id, data, created_at) VALUES (?, ?, ?)",
            (asset.file_id, json.dumps(asset.model_dump(), ensure_ascii=False), asset.created_at),
        )


def _ensure_table() -> None:
    with db.conn() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seed_assets (
                file_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def _validate_id(value: str) -> None:
    if (
        not value
        or len(value) > 128
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise SeedAssetStoreError("INVALID_ASSET_ID", "素材标识不能包含路径")


def _safe_name(value: str, *, fallback: str) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name or fallback)[:255]


def _collect_file_ids(value, output: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_id", "source_file_id", "clip_file_id"} and isinstance(item, str):
                output.add(item)
            else:
                _collect_file_ids(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_file_ids(item, output)
