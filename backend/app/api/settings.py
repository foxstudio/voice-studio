from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import AppSettings, MimoSecretUpdate
from app.services import settings_store

router = APIRouter()


@router.get("", response_model=AppSettings)
async def get_settings():
    return settings_store.get()


@router.patch("", response_model=AppSettings)
async def update_settings(settings: AppSettings):
    return settings_store.update(settings)


@router.patch("/mimo-secret", response_model=AppSettings)
async def update_mimo_secret(data: MimoSecretUpdate):
    return settings_store.update_mimo_api_key(data.api_key, data.clear)
