from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.errors import AppException
from app.schemas.waveform import WaveformPeaksResponse
from app.schemas.voice_studio import HistoryItem
from app.services import (
    download_counter,
    history_artifact_service,
    history_store,
    waveform_cache,
)
from app.services.media_types import browser_audio_media_type

router = APIRouter()


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HistoryItem]
    total: int
    offset: int
    limit: int


@router.get("", response_model=list[HistoryItem])
def list_history(
    limit: int = 100,
    offset: int = 0,
    project_id: str | None = None,
    segment_id: str | None = None,
    source: str | None = None,
):
    return history_store.list_history(
        limit,
        offset,
        project_id=project_id,
        segment_id=segment_id,
        source=source,
    )


@router.get("/page", response_model=HistoryPage)
def list_history_page(
    limit: int = 40,
    offset: int = 0,
    project_id: str | None = None,
    segment_id: str | None = None,
    source: str | None = None,
):
    bounded_limit = max(1, min(100, limit))
    bounded_offset = max(0, offset)
    filters = {
        "project_id": project_id,
        "segment_id": segment_id,
        "source": source,
    }
    return HistoryPage(
        items=history_store.list_history(
            bounded_limit,
            bounded_offset,
            **filters,
        ),
        total=history_store.count_history(**filters),
        offset=bounded_offset,
        limit=bounded_limit,
    )


@router.delete("/{result_id}")
def delete_history(result_id: str):
    history_artifact_service.delete_result(result_id)
    return {"status": "deleted"}


@router.get("/{result_id}/audio")
def get_audio(result_id: str, download: bool = False, filename: str | None = None):
    path = history_store.audio_path(result_id)
    if not path:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    if download:
        sequence = download_counter.next_history_audio_sequence()
        base_filename = _safe_download_filename(filename, path.name)
        return FileResponse(
            path,
            filename=f"{sequence:03d}-{base_filename}",
            media_type=browser_audio_media_type(path),
        )
    return FileResponse(
        path,
        media_type=browser_audio_media_type(path),
        headers={"Cache-Control": "private, max-age=3600, immutable"},
    )


@router.get("/{result_id}/waveform", response_model=WaveformPeaksResponse)
async def get_waveform(result_id: str, bins: int = 320):
    return await asyncio.to_thread(_history_waveform, result_id, bins)


def _history_waveform(result_id: str, bins: int) -> dict:
    path = history_store.audio_path(result_id)
    if not path:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return waveform_cache.waveform_peaks(
        path,
        result_id=result_id,
        bins=bins,
    )


def _safe_download_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    value = "".join("_" if ch in '\\/:*?"<>|\0\r\n\t' else ch for ch in filename).strip(" .")
    return value[:120] or fallback
