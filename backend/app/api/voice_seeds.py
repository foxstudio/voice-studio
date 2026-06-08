from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.models.exceptions import AppException
from app.models.schemas import VoiceSeed, VoiceSeedImportRequest
from app.services import voice_seed_store

router = APIRouter()


@router.get("", response_model=list[VoiceSeed])
async def list_voice_seeds():
    return voice_seed_store.list_seeds()


@router.post("/import", response_model=VoiceSeed)
async def import_voice_seed(data: VoiceSeedImportRequest):
    try:
        return voice_seed_store.import_seed(data.seed_id)
    except ValueError:
        raise AppException(404, "VOICE_SEED_NOT_FOUND", "Voice seed not found") from None
    except Exception as exc:
        raise AppException(502, "VOICE_SEED_IMPORT_FAILED", f"Voice seed import failed: {exc}") from exc


@router.get("/{seed_id}/audio")
async def preview_voice_seed_audio(seed_id: str):
    seed = voice_seed_store.get_seed(seed_id)
    if not seed:
        raise AppException(404, "VOICE_SEED_NOT_FOUND", "Voice seed not found")
    return RedirectResponse(seed.download_url)
