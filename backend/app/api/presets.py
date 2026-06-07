from __future__ import annotations

from fastapi import APIRouter

from app.models.exceptions import AppException
from app.models.schemas import PresetTemplate
from app.services import preset_store

router = APIRouter()


@router.get("", response_model=list[PresetTemplate])
async def list_presets():
    return preset_store.list_presets()


@router.get("/{preset_id}", response_model=PresetTemplate)
async def get_preset(preset_id: str):
    preset = preset_store.get_preset(preset_id)
    if not preset:
        raise AppException(404, "PRESET_NOT_FOUND", "Preset not found")
    return preset
