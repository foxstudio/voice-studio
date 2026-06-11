from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import AppException
from app.services import audio_tools, settings_store

router = APIRouter()


class AudioToolRequest(BaseModel):
    audio_ids: list[str]
    format: str = "wav"
    normalize: bool = False
    trim_silence: bool = False
    silence_ms: int = 300


@router.post("/merge")
async def merge_audio(req: AudioToolRequest):
    paths = []
    for audio_id in req.audio_ids:
        for ext in ["wav", "mp3", "flac"]:
            path = settings_store.output_dir() / f"{audio_id}.{ext}"
            if path.exists():
                paths.append(path)
                break
    if not paths:
        raise AppException(400, "AUDIO_NOT_FOUND", "No audio files found")
    dest = settings_store.export_dir() / f"merge-{len(paths)}-{req.silence_ms}.{req.format}"
    audio_tools.merge_files(paths, dest, req.format, req.silence_ms, req.normalize)
    return {"path": str(dest), "count": len(paths)}

