from __future__ import annotations

from fastapi import APIRouter

from app.models.exceptions import AppException
from app.models.schemas import PresetTemplate, PresetTemplateUpsert
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


@router.post("", response_model=PresetTemplate)
async def create_preset(payload: PresetTemplateUpsert):
    try:
        return preset_store.save_preset(payload)
    except ValueError as exc:
        if str(exc) == "BUILTIN_PRESET_READONLY":
            raise AppException(409, "BUILTIN_PRESET_READONLY", "Built-in presets cannot be edited")
        raise


@router.patch("/{preset_id}", response_model=PresetTemplate)
async def update_preset(preset_id: str, payload: PresetTemplateUpsert):
    if not preset_store.get_preset(preset_id):
        raise AppException(404, "PRESET_NOT_FOUND", "Preset not found")
    try:
        return preset_store.save_preset(payload.model_copy(update={"preset_id": preset_id}))
    except ValueError as exc:
        if str(exc) == "BUILTIN_PRESET_READONLY":
            raise AppException(409, "BUILTIN_PRESET_READONLY", "Built-in presets cannot be edited")
        raise


@router.delete("/{preset_id}")
async def delete_preset(preset_id: str):
    try:
        deleted = preset_store.delete_preset(preset_id)
    except ValueError as exc:
        if str(exc) == "BUILTIN_PRESET_READONLY":
            raise AppException(409, "BUILTIN_PRESET_READONLY", "Built-in presets cannot be deleted")
        raise
    if not deleted:
        raise AppException(404, "PRESET_NOT_FOUND", "Preset not found")
    return {"status": "deleted", "preset_id": preset_id}
