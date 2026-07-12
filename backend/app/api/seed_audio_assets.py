from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile, status

from app.errors import AppException
from app.services import seed_asset_store


router = APIRouter()
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_EXTENSION_FORMATS = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
}


@router.post("/image", status_code=status.HTTP_201_CREATED)
async def upload_seed_audio_image(
    file: UploadFile = File(...),
    license_status: Literal["self_voice", "authorized", "company_authorized", "test_only"] = Form(...),
):
    content = await file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise AppException(400, "SEED_IMAGE_EMPTY", "参考图片不能为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise AppException(413, "SEED_IMAGE_TOO_LARGE", "参考图片不能超过 10 MB")

    try:
        detected = seed_asset_store.decode_image_format(content)
    except seed_asset_store.SeedAssetStoreError as exc:
        raise AppException(400, exc.code, str(exc)) from exc
    suffix_format = _EXTENSION_FORMATS.get(Path(file.filename or "").suffix.lower())
    if suffix_format is None or suffix_format != detected:
        raise AppException(400, "SEED_IMAGE_FORMAT_MISMATCH", "图片扩展名必须与 JPEG、PNG 或 WebP 内容一致")

    asset = seed_asset_store.register_image(
        content=content,
        original_name=file.filename or f"image.{detected}",
        media_format=detected,
        license_status=license_status,
    )
    return asset.public_metadata()


@router.get("/{file_id}")
async def get_seed_audio_asset(file_id: str):
    try:
        asset = seed_asset_store.get_asset(file_id)
    except seed_asset_store.SeedAssetStoreError as exc:
        raise AppException(400, exc.code, str(exc)) from exc
    if asset is None:
        raise AppException(404, "SEED_ASSET_NOT_FOUND", "Seed Audio 素材不存在")
    return asset.public_metadata()


@router.delete("/{file_id}")
async def delete_seed_audio_asset(file_id: str):
    try:
        asset = seed_asset_store.get_asset(file_id)
        if asset is not None and asset.source == "preset":
            raise AppException(403, "SEED_PRESET_DELETE_FORBIDDEN", "预设素材不能通过上传素材接口删除")
        if asset is not None and seed_asset_store.is_asset_referenced(file_id):
            raise AppException(409, "SEED_ASSET_IN_USE", "该素材仍被排队任务或历史记录引用")
        deleted = seed_asset_store.delete_asset(file_id)
    except seed_asset_store.SeedAssetStoreError as exc:
        status_code = 409 if exc.code == "ASSET_PATH_NOT_MANAGED" else 400
        raise AppException(status_code, exc.code, str(exc)) from exc
    return {"status": "deleted" if deleted else "already_absent", "file_id": file_id, "deleted": deleted}
