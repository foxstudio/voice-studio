from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import AppSettings, MimoSecretUpdate
from app.services import settings_store

router = APIRouter()


class StorageLocation(BaseModel):
    key: str
    label: str
    path: str
    category: str
    description: str
    exists: bool
    size_bytes: int
    file_count: int
    truncated: bool
    cleanup_key: str | None = None
    cleanup_label: str | None = None
    cleanup_risk: str | None = None


class StorageFlow(BaseModel):
    name: str
    path: str
    description: str


class StorageAuditResponse(BaseModel):
    locations: list[StorageLocation]
    flows: list[StorageFlow]
    total_bytes: int


class StorageCleanupRequest(BaseModel):
    targets: list[str]


class StorageCleanupItem(BaseModel):
    target: str
    path: str
    before_bytes: int
    after_bytes: int
    removed_bytes: int
    before_files: int
    after_files: int


class StorageCleanupResponse(BaseModel):
    cleaned: list[StorageCleanupItem]
    skipped: list[str]
    removed_bytes: int


class StorageOpenRequest(BaseModel):
    key: str


class StorageOpenResponse(BaseModel):
    status: str
    key: str
    path: str


@router.get("", response_model=AppSettings)
async def get_settings():
    return settings_store.get()


@router.patch("", response_model=AppSettings)
async def update_settings(settings: AppSettings):
    return settings_store.update(settings)


@router.patch("/mimo-secret", response_model=AppSettings)
async def update_mimo_secret(data: MimoSecretUpdate):
    return settings_store.update_mimo_api_key(data.api_key, data.clear)


@router.get("/storage", response_model=StorageAuditResponse)
async def get_storage_audit():
    return settings_store.storage_audit()


@router.post("/storage/cleanup", response_model=StorageCleanupResponse)
async def cleanup_storage(data: StorageCleanupRequest):
    return settings_store.cleanup_storage(data.targets)


@router.post("/storage/open", response_model=StorageOpenResponse)
async def open_storage_location(data: StorageOpenRequest):
    try:
        return settings_store.open_storage_location(data.key)
    except ValueError as exc:
        raise AppException(404, "STORAGE_LOCATION_NOT_FOUND", str(exc)) from exc
