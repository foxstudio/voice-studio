from __future__ import annotations

from pydantic import BaseModel

from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.domains.video_localization.tts_selection import (
    TtsSelectionRequest,
    TtsSourceSnapshot,
    TtsTargetSnapshot,
    build_selection_snapshot,
)
from app.domains.video_localization import tts_orchestration
from app.schemas.voice_studio import GenerateRequest


PARAMETER_PACK_VERSION = "video-localization-tts-parameter-pack-v2"


class TtsParameterPack(BaseModel):
    version: str = PARAMETER_PACK_VERSION
    project_id: str
    target: TtsTargetSnapshot
    source: TtsSourceSnapshot
    request: GenerateRequest


def build_parameter_pack(
    *,
    project_id: str,
    draft: VideoLocalizationDraft,
    selection_request: TtsSelectionRequest,
    reference_start_ms: int | None = None,
    reference_end_ms: int | None = None,
) -> TtsParameterPack:
    selection = build_selection_snapshot(draft, selection_request)
    request = tts_orchestration.build_selection_handoff(
        project_id,
        draft,
        selection,
        reference_start_ms=reference_start_ms,
        reference_end_ms=reference_end_ms,
    )
    return TtsParameterPack(
        project_id=project_id,
        target=selection.target,
        source=selection.source,
        request=request,
    )
