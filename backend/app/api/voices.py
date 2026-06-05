"""声音资产库 API"""

from fastapi import APIRouter, HTTPException, UploadFile, File

from app.models.schemas import VoiceAsset, VoiceAssetCreate
from app.services import voice_store

router = APIRouter()


@router.get("", response_model=list[VoiceAsset])
async def list_voices():
    return voice_store.list_voices()


@router.post("", response_model=VoiceAsset)
async def create_voice(data: VoiceAssetCreate):
    return voice_store.create_voice(data)


@router.get("/{voice_id}", response_model=VoiceAsset)
async def get_voice(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice


@router.patch("/{voice_id}", response_model=VoiceAsset)
async def update_voice(voice_id: str, data: VoiceAssetCreate):
    voice = voice_store.update_voice(voice_id, data)
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    voice_store.delete_voice(voice_id)
    return {"status": "deleted"}


@router.post("/upload")
async def upload_reference_audio(file: UploadFile = File(...)):
    file_id = await voice_store.upload_audio(file)
    return {"file_id": file_id, "filename": file.filename}
