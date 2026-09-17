from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter

from app.errors import AppException
from app.schemas.voice_studio import TTSVerificationRequest, TTSVerificationResponse, TranscriptionRecord
from app.services import asr_service, audio_tools, database as db, history_store, task_queue, text_verifier

router = APIRouter()

@router.post("/tts-verification", response_model=TTSVerificationResponse)
async def verify_tts_output(body: TTSVerificationRequest):
    expected_text = (body.expected_text or "").strip()
    transcript_text = (body.transcript_text or "").strip()
    transcription_id = None

    if body.result_id:
        item = history_store.get(body.result_id)
        if not item:
            raise AppException(404, "RESULT_NOT_FOUND", "Generation result not found")
        expected_text = expected_text or task_queue.verification_expected_text_for_result(
            body.result_id,
            input_text=item.input_text,
            engine_id=item.engine_id,
        )
        if item.engine_id == text_verifier.SEED_AUDIO_ENGINE_ID and not expected_text:
            report = text_verifier.skipped_non_speech_report(
                original_prompt=item.input_text,
                result_id=body.result_id,
                asr_engine_id=body.asr_engine_id,
            )
            task_queue.attach_verification_to_result(body.result_id, report)
            return report
        if not transcript_text:
            audio_path = history_store.audio_path(body.result_id)
            if not audio_path:
                raise AppException(404, "AUDIO_NOT_FOUND", "Result audio not found")
            suffix = audio_path.suffix.lower() or ".wav"
            asr_path = audio_path
            if suffix not in {".wav", ".mp3"}:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    audio_tools.convert_file(audio_path, tmp.name, "wav")
                    asr_path = Path(tmp.name)
                    suffix = ".wav"
            asr_service.validate_request(body.asr_engine_id, body.language, suffix)
            result = asr_service.transcribe(engine_id=body.asr_engine_id, audio_path=str(asr_path), language=body.language)
            if asr_path != audio_path:
                asr_path.unlink(missing_ok=True)
            record = TranscriptionRecord(
                engine_id=body.asr_engine_id,
                filename=audio_path.name,
                language=body.language,
                text=result["text"],
                segments=asr_service.normalize_segments(result.get("segments")),
                size_bytes=audio_path.stat().st_size if audio_path.exists() else 0,
                usage_seconds=result.get("usage_seconds"),
                provider_response_id=result.get("provider_response_id"),
            )
            for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
                setattr(record, key, value)
            record.has_source_audio = False
            db.upsert("transcriptions", record.transcription_id, record.model_dump(), "created_at")
            transcript_text = record.text
            transcription_id = record.transcription_id

    if not expected_text:
        raise AppException(400, "EXPECTED_TEXT_REQUIRED", "expected_text or a valid result_id is required")
    if not transcript_text:
        raise AppException(400, "TRANSCRIPT_TEXT_REQUIRED", "transcript_text or a result_id with available audio is required")

    report = text_verifier.verify_transcript(
        expected_text=expected_text,
        transcript_text=transcript_text,
        result_id=body.result_id,
        transcription_id=transcription_id,
        asr_engine_id=body.asr_engine_id if body.result_id else None,
    )
    if body.result_id:
        task_queue.attach_verification_to_result(body.result_id, report)
    return report
