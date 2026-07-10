from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import HistoryItem
from app.services import history_store, waveform_cache

router = APIRouter()


class WaveformPeaksResponse(BaseModel):
    peaks: list[float]
    duration: float
    bins: int


@router.get("", response_model=list[HistoryItem])
async def list_history(limit: int = 100, offset: int = 0):
    return history_store.list_history(limit, offset)


@router.delete("/{result_id}")
async def delete_history(result_id: str):
    history_store.delete(result_id)
    return {"status": "deleted"}


@router.get("/{result_id}/audio")
async def get_audio(result_id: str, download: bool = False, filename: str | None = None):
    path = history_store.audio_path(result_id)
    if not path:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    if download:
        return FileResponse(path, filename=_safe_download_filename(filename, path.name))
    return FileResponse(path)


@router.get("/{result_id}/waveform", response_model=WaveformPeaksResponse)
async def get_waveform(result_id: str, bins: int = 320):
    path = history_store.audio_path(result_id)
    if not path:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return await asyncio.to_thread(waveform_cache.waveform_peaks, path, result_id=result_id, bins=bins)


def _safe_download_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    value = "".join("_" if ch in '\\/:*?"<>|\0\r\n\t' else ch for ch in filename).strip(" .")
    return value[:120] or fallback
