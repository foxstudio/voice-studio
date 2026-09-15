"""Optional raw-material observations, never timeline admission decisions."""

from __future__ import annotations

from app.schemas.video_localization_dubbing_production import (
    DubbingTranscriptComparison,
)
from app.schemas.tts_content import CONTENT_ASR_ENGINE, TtsContentEvidence


def reusable_transcript(evidence: TtsContentEvidence | None, audio_sha256: str,
                        *, engine_id: str = CONTENT_ASR_ENGINE) -> bool:
    return bool(evidence and evidence.matches_audio(audio_sha256, engine_id=engine_id))


def content_finding(comparison: DubbingTranscriptComparison) -> tuple[str, str, str] | None:
    """A whole-file transcript cannot certify or reject an edited selection."""
    if comparison.missing_tokens or comparison.extra_tokens:
        return ("CANDIDATE_TRANSCRIPT_MINOR_MISMATCH", "warning",
                "原始素材的识别台词与目标有差异，需在剪辑后核对保留部分。")
    return None
