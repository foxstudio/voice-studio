from __future__ import annotations

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import VoiceAsset, VoiceAssetCreate, VoiceAssetUpdate
from app.services import voice_store

router = APIRouter()


@router.get("", response_model=list[VoiceAsset])
async def list_voices(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1)):
    return voice_store.list_voices(offset=offset, limit=limit)


@router.post("", response_model=VoiceAsset)
async def create_voice(data: VoiceAssetCreate):
    return voice_store.create_voice(data)


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


@router.post("/upload")
async def upload_reference_audio(file: UploadFile = File(...)):
    return await voice_store.upload_audio(file)


@router.get("/{voice_id}/audio/{file_id}")
async def get_reference_audio(voice_id: str, file_id: str):
    voice = voice_store.get_voice(voice_id)
    vf = voice_store.get_file(file_id)
    if not voice or file_id not in voice.reference_audio_ids or not vf:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return FileResponse(vf.path)
