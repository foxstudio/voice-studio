from fastapi import APIRouter
"""历史记录 API"""

from app.models.exceptions import AppException

from app.models.schemas import HistoryItem
from app.services import history_store

router = APIRouter()


@router.get("", response_model=list[HistoryItem])
async def list_history(limit: int = 50, offset: int = 0):
    return history_store.list_history(limit=limit, offset=offset)


@router.delete("/{result_id}")
async def delete_history(result_id: str):
    history_store.delete(result_id)
    return {"status": "deleted"}


@router.get("/{result_id}/audio")
async def get_audio(result_id: str):
    path = history_store.get_audio_path(result_id)
    if not path:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")
