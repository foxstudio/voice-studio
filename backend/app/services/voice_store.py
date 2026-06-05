"""声音资产存储"""

import os
import shutil
import uuid

from fastapi import UploadFile

from app.models.schemas import VoiceAsset, VoiceAssetCreate

_VOICES: dict[str, VoiceAsset] = {}

UPLOAD_DIR = os.path.expanduser("~/VoiceStudio/voices")


def _ensure_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def list_voices() -> list[VoiceAsset]:
    return list(_VOICES.values())


def get_voice(voice_id: str) -> VoiceAsset | None:
    return _VOICES.get(voice_id)


def create_voice(data: VoiceAssetCreate) -> VoiceAsset:
    voice = VoiceAsset(**data.model_dump())
    _VOICES[voice.voice_id] = voice
    return voice


def update_voice(voice_id: str, data: VoiceAssetCreate) -> VoiceAsset | None:
    if voice_id not in _VOICES:
        return None
    voice = _VOICES[voice_id]
    for k, v in data.model_dump().items():
        setattr(voice, k, v)
    return voice


def delete_voice(voice_id: str) -> None:
    _VOICES.pop(voice_id, None)


async def upload_audio(file: UploadFile) -> str:
    _ensure_dir()
    file_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "audio.wav")[1]
    dest = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    with open(dest, "wb") as f:
        content = await file.read()
        f.write(content)
    return file_id
