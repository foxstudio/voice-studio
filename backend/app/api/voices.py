from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import LicenseStatus, VoiceAsset, VoiceAssetCreate, VoiceAssetUpdate, VoiceType
from app.services import voice_store

router = APIRouter()


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AppException(400, "VOICE_TAGS_INVALID", "tags must be a JSON array or comma-separated string") from exc
        if not isinstance(parsed, list):
            raise AppException(400, "VOICE_TAGS_INVALID", "tags must be a JSON array or comma-separated string")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in stripped.split(",") if part.strip()]


@router.get("", response_model=list[VoiceAsset])
async def list_voices(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1)):
    return voice_store.list_voices(offset=offset, limit=limit)


@router.post("", response_model=VoiceAsset)
async def create_voice(data: VoiceAssetCreate):
    return voice_store.create_voice(data)


@router.post("/upload")
async def upload_reference_audio(file: UploadFile = File(...)):
    return await voice_store.upload_audio(file)


@router.post("/register", response_model=VoiceAsset)
async def register_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    reference_text: str = Form(""),
    license_status: LicenseStatus = Form(LicenseStatus.unknown),
    tags: str = Form(""),
    voice_type: VoiceType = Form(VoiceType.test_sample),
    description: str = Form(""),
    default_language: str = Form("zh"),
    recommended_engine_id: str | None = Form(None),
):
    uploaded = await voice_store.upload_audio(file)
    data = VoiceAssetCreate(
        name=name,
        voice_type=voice_type,
        description=description,
        default_language=default_language,
        tags=_parse_tags(tags),
        reference_text=reference_text,
        recommended_engine_id=recommended_engine_id,
        reference_audio_ids=[uploaded["file_id"]],
        license_status=license_status,
    )
    return voice_store.create_voice(data)


@router.get("/files/{file_id}/audio")
async def get_voice_file_audio(file_id: str):
    vf = voice_store.get_file(file_id)
    if not vf or not vf.path or not Path(vf.path).exists():
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return FileResponse(vf.path)


@router.get("/{voice_id}", response_model=VoiceAsset)
async def get_voice(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.patch("/{voice_id}", response_model=VoiceAsset)
async def update_voice(voice_id: str, data: VoiceAssetUpdate):
    voice = voice_store.update_voice(voice_id, data)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    voice_store.delete_voice(voice_id)
    return {"status": "deleted"}


@router.get("/{voice_id}/audio/{file_id}")
async def get_reference_audio(voice_id: str, file_id: str):
    voice = voice_store.get_voice(voice_id)
    vf = voice_store.get_file(file_id)
    if not voice or file_id not in voice.reference_audio_ids or not vf:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return FileResponse(vf.path)
