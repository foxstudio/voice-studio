"""Unprompted transcription of generated audio, without creating ASR history."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.errors import AppException
from app.schemas.tts_content import CONTENT_ASR_ENGINE, CONTENT_ASR_PROTOCOL, TtsContentEvidence
from app.services import asr_service


def acquire_content_evidence(audio_path: Path, cached: TtsContentEvidence | None = None,
                             *, engine_id: str = CONTENT_ASR_ENGINE) -> TtsContentEvidence:
    """Cache only exact-byte/config-identical complete transcription, not judgment."""
    try:
        digest = _audio_sha256(audio_path)
    except OSError as exc:
        raise AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE",
                           "无法读取生成音频，内容检查未完成；请先恢复音频文件。") from exc
    if cached and cached.matches_audio(digest, engine_id=engine_id):
        return cached
    try:
        result = asr_service.transcribe(engine_id=engine_id, audio_path=str(audio_path),
                                        language="auto")
        transcript = str(result.get("text") or "").strip()
        incomplete = bool(result.get("incomplete_chunk_ranges"))
        error = "ASR_INCOMPLETE" if incomplete else "ASR_EMPTY" if not transcript else None
    except Exception as exc:
        transcript = ""
        error = str(getattr(exc, "code", None) or "ASR_UNAVAILABLE")
    # An output replaced during inference cannot be certified by this transcript.
    try:
        if _audio_sha256(audio_path) != digest:
            error = "AUDIO_CHANGED_DURING_ASR"
    except OSError:
        error = "AUDIO_UNAVAILABLE_AFTER_ASR"
    return TtsContentEvidence(audio_sha256=digest, engine_id=engine_id,
                              protocol=CONTENT_ASR_PROTOCOL,
                              status="unavailable" if error else "complete",
                              transcript=transcript, error_code=error)


def _audio_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
