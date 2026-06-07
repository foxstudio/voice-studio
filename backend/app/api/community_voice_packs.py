from __future__ import annotations

from fastapi import APIRouter

from app.models.exceptions import AppException
from app.models.schemas import CommunityVoicePack, CommunityVoicePackImportRequest
from app.services import community_voice_pack_store

router = APIRouter()


@router.get("", response_model=list[CommunityVoicePack])
async def list_community_voice_packs():
    return community_voice_pack_store.list_packs()


@router.post("/import", response_model=CommunityVoicePack)
async def import_community_voice_pack(data: CommunityVoicePackImportRequest):
    try:
        return community_voice_pack_store.import_pack(data.pack_id, data.candidate_ids)
    except ValueError as exc:
        code = str(exc)
        if code == "COMMUNITY_VOICE_PACK_NOT_FOUND":
            raise AppException(404, code, "Community voice pack not found") from None
        if code == "COMMUNITY_VOICE_CANDIDATE_NOT_FOUND":
            raise AppException(404, code, "Community voice candidate not found") from None
        raise AppException(400, "COMMUNITY_VOICE_PACK_IMPORT_INVALID", code) from exc
    except Exception as exc:
        raise AppException(502, "COMMUNITY_VOICE_PACK_IMPORT_FAILED", f"Community voice pack import failed: {exc}") from exc
