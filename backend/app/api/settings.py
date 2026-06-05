"""设置中心 API"""

from fastapi import APIRouter

from app.models.schemas import AppSettings
from app.services import settings_store

router = APIRouter()


@router.get("", response_model=AppSettings)
async def get_settings():
    return settings_store.get()


@router.patch("", response_model=AppSettings)
async def update_settings(data: AppSettings):
    return settings_store.update(data)
