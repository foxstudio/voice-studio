"""声音资产存储 - SQLite 持久化"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.models.schemas import VoiceAsset, VoiceAssetCreate
from app.services import database as db

UPLOAD_DIR = os.path.expanduser("~/VoiceStudio/voices")

logger = logging.getLogger(__name__)


def _ensure_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def list_voices() -> list[VoiceAsset]:
    return [VoiceAsset(**d) for d in db.db_list_voices()]


def get_voice(voice_id: str) -> VoiceAsset | None:
    d = db.db_get_voice(voice_id)
    return VoiceAsset(**d) if d else None


def create_voice(data: VoiceAssetCreate) -> VoiceAsset:
    voice = VoiceAsset(**data.model_dump())
    db.db_save_voice(voice.model_dump())
    return voice


def update_voice(voice_id: str, data: VoiceAssetCreate) -> VoiceAsset | None:
    existing = db.db_get_voice(voice_id)
    if not existing:
        return None
    merged = {**existing, **data.model_dump(), "voice_id": voice_id,
              "updated_at": datetime.now().isoformat()}
    voice = VoiceAsset(**merged)
    db.db_save_voice(voice.model_dump())
    return voice


def delete_voice(voice_id: str) -> None:
    voice = db.db_get_voice(voice_id)
    if voice:
        for audio_id in voice.get("reference_audio_ids", []):
            deleted = False
            for ext in [".wav", ".mp3", ".flac", ".ogg"]:
                path = Path(UPLOAD_DIR) / f"{audio_id}{ext}"
                if path.exists():
                    path.unlink()
                    logger.info("Deleted voice file: %s (voice_id=%s)", path, voice_id)
                    deleted = True
                    break
            if not deleted:
                logger.warning("Voice file not found for audio_id=%s (voice_id=%s)", audio_id, voice_id)
    db.db_delete_voice(voice_id)


async def upload_audio(file: UploadFile) -> str:
    _ensure_dir()
    file_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "audio.wav")[1]
    dest = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    with open(dest, "wb") as f:
        content = await file.read()
        f.write(content)
    return file_id
