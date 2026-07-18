from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import (
    DoubaoVoiceCloneResponse,
    DoubaoVoiceCloneTrainRequest,
    LicenseStatus,
    TranscriptionRecord,
    VoiceAsset,
    VoiceAssetCreate,
    VoiceAssetUpdate,
    VoiceClipRequest,
    VoiceClipResponse,
    VoiceClipTranscribeRequest,
    VoiceClipTranscribeResponse,
    VoiceType,
)
from app.services import asr_service, audio_tools, database as db, doubao_client, settings_store, voice_store

router = APIRouter()


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AppException(400, "VOICE_TAGS_INVALID", "tags must be a JSON array or comma-separated string") from exc
        if not isinstance(parsed, list):
            raise AppException(400, "VOICE_TAGS_INVALID", "tags must be a JSON array or comma-separated string")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in stripped.split(",") if part.strip()]


def _safe_doubao_speaker_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return safe.strip("_")[:64] or "voice_studio_voice"


def _doubao_binding_for(voice: VoiceAsset, engine_id: str):
    return next((binding for binding in voice.engine_bindings if binding.engine_id == engine_id), None)


def _doubao_status_from_summary(summary: dict) -> str:
    status = summary.get("status")
    if status is None:
        return "submitted"
    return str(status)


def _doubao_metadata(summary: dict, *, custom_speaker_id: str | None = None) -> dict:
    metadata = dict(summary)
    if custom_speaker_id:
        metadata["custom_speaker_id"] = custom_speaker_id
    return metadata


def _doubao_error_message(exc: doubao_client.DoubaoAPIError) -> str:
    message = f"{exc} logid={exc.logid or '-'}"
    if exc.body:
        message += f" body={exc.body}"
    return message


def _doubao_custom_speaker_id(voice: VoiceAsset) -> str | None:
    if isinstance(voice.external_metadata, dict):
        raw_custom = voice.external_metadata.get("custom_speaker_id")
        return str(raw_custom) if raw_custom else None
    return None


def _transcribe_existing_audio(*, path: str, filename: str, language: str, engine_id: str) -> TranscriptionRecord:
    suffix = Path(filename or path).suffix.lower() or ".wav"
    asr_service.validate_request(engine_id, language, suffix)
    audio_path = Path(path)
    if not audio_path.exists():
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")

    duration_ms = None
    try:
        duration_ms = audio_tools.probe_audio(audio_path).get("duration_ms")
    except Exception:
        duration_ms = None

    result = asr_service.transcribe(engine_id=engine_id, audio_path=str(audio_path), language=language)
    record = TranscriptionRecord(
        engine_id=engine_id,
        filename=filename or audio_path.name,
        language=language,
        text=result["text"],
        segments=asr_service.normalize_segments(result.get("segments")),
        duration_ms=duration_ms,
        size_bytes=audio_path.stat().st_size,
        usage_seconds=result.get("usage_seconds"),
        provider_response_id=result.get("provider_response_id"),
    )
    for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
        setattr(record, key, value)
    db.upsert(
        "transcriptions",
        record.transcription_id,
        {**record.model_dump(), "source_audio_path": str(audio_path)},
        "created_at",
    )
    return record


@router.get("", response_model=list[VoiceAsset])
async def list_voices(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1)):
    return voice_store.list_voices(offset=offset, limit=limit)


@router.post("", response_model=VoiceAsset)
async def create_voice(data: VoiceAssetCreate):
    return voice_store.create_voice(data)


@router.post("/upload")
async def upload_reference_audio(file: UploadFile = File(...)):
    return await voice_store.upload_audio(file)


@router.get("/doubao/cloud")
async def list_doubao_cloud_voices():
    voices = [
        voice
        for voice in voice_store.list_voices(offset=0, limit=10000)
        if voice.external_provider == "doubao" and voice.external_voice_id
    ]
    return {
        "voices": voices,
        "count": len(voices),
        "management": {
            "local_unbind_supported": True,
            "cloud_delete_supported": False,
            "cloud_delete_note": "Voice Studio 只能删除本地音色或解除本地绑定；云端 SpeakerID 删除/续费/订单管理请使用火山引擎控制台相关接口。",
            "official_docs": [
                "https://www.volcengine.com/docs/6561/1801953",
                "https://www.volcengine.com/docs/6561/2235883",
            ],
        },
    }


@router.post("/doubao/cloud/refresh")
async def refresh_doubao_cloud_voices():
    settings = settings_store.get()
    api_key = settings_store.doubao_api_key()
    if not api_key:
        raise AppException(400, "DOUBAO_API_KEY_REQUIRED", "请先在设置中配置豆包 API Key")
    voices = [
        voice
        for voice in voice_store.list_voices(offset=0, limit=10000)
        if voice.external_provider == "doubao" and voice.external_voice_id
    ]
    refreshed: list[VoiceAsset] = []
    failed: list[dict] = []
    for voice in voices:
        try:
            response = doubao_client.get_voice(
                base_url=settings.doubao_base_url,
                api_key=api_key,
                resource_id=settings.doubao_default_icl_resource_id,
                speaker_id=voice.external_voice_id or "",
                custom_speaker_id=_doubao_custom_speaker_id(voice),
            )
            summary = doubao_client.summarize_voice_status(response, speaker_id=voice.external_voice_id or "")
            updated = voice_store.update_external_binding(
                voice.voice_id,
                provider="doubao",
                external_voice_id=voice.external_voice_id or "",
                status=_doubao_status_from_summary(summary),
                metadata=_doubao_metadata(summary, custom_speaker_id=_doubao_custom_speaker_id(voice)),
            )
            if updated:
                refreshed.append(updated)
        except doubao_client.DoubaoAPIError as exc:
            failed.append({"voice_id": voice.voice_id, "voice_name": voice.name, "message": _doubao_error_message(exc)})
    return {"voices": refreshed, "failed": failed, "count": len(refreshed)}


@router.post("/register", response_model=VoiceAsset)
async def register_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    reference_text: str = Form(""),
    license_status: LicenseStatus = Form(LicenseStatus.unknown),
    tags: str = Form(""),
    voice_type: VoiceType = Form(VoiceType.test_sample),
    description: str = Form(""),
    default_language: str = Form("zh"),
    recommended_engine_id: str | None = Form(None),
):
    uploaded = await voice_store.upload_audio(file)
    data = VoiceAssetCreate(
        name=name,
        voice_type=voice_type,
        description=description,
        default_language=default_language,
        tags=_parse_tags(tags),
        reference_text=reference_text,
        recommended_engine_id=recommended_engine_id,
        reference_audio_ids=[uploaded["file_id"]],
        license_status=license_status,
    )
    return voice_store.create_voice(data)


@router.get("/files/{file_id}/audio")
async def get_voice_file_audio(file_id: str):
    vf = voice_store.get_file(file_id)
    if not vf or not vf.path or not Path(vf.path).exists():
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return FileResponse(vf.path)


@router.post("/files/{file_id}/clip", response_model=VoiceClipResponse)
async def clip_voice_file(file_id: str, data: VoiceClipRequest):
    """Create a managed audio clip without starting transcription."""

    return voice_store.create_audio_clip(file_id, data.start_ms, data.end_ms)


@router.post("/files/{file_id}/clip-transcribe", response_model=VoiceClipTranscribeResponse)
async def clip_and_transcribe_voice_file(file_id: str, data: VoiceClipTranscribeRequest):
    clip = voice_store.create_audio_clip(file_id, data.start_ms, data.end_ms)
    try:
        transcription = _transcribe_existing_audio(
            path=clip["path"],
            filename=clip["filename"],
            language=data.language,
            engine_id=data.engine_id,
        )
    except Exception:
        voice_store.delete_file(clip["file_id"])
        raise
    return {**clip, "transcription": transcription}


@router.get("/{voice_id}", response_model=VoiceAsset)
async def get_voice(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.patch("/{voice_id}", response_model=VoiceAsset)
async def update_voice(voice_id: str, data: VoiceAssetUpdate):
    voice = voice_store.update_voice(voice_id, data)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.post("/{voice_id}/doubao/clone-train", response_model=DoubaoVoiceCloneResponse)
async def train_doubao_voice_clone(voice_id: str, data: DoubaoVoiceCloneTrainRequest):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    train_binding = _doubao_binding_for(voice, "doubao-voice-clone-train")
    if not train_binding or not train_binding.available:
        raise AppException(400, "DOUBAO_VOICE_NOT_TRAINABLE", train_binding.reason if train_binding else "当前音色不能训练豆包云端音色")
    settings = settings_store.get()
    api_key = settings_store.doubao_api_key()
    if not api_key:
        raise AppException(400, "DOUBAO_API_KEY_REQUIRED", "请先在设置中配置豆包 API Key")
    if settings.doubao_upload_confirm and not data.confirm_upload:
        raise AppException(400, "DOUBAO_UPLOAD_CONFIRM_REQUIRED", "豆包音色训练会上传参考音频，请确认后再继续")
    file_id = voice.reference_audio_ids[0] if voice.reference_audio_ids else ""
    vf = voice_store.get_file(file_id)
    if not vf or not Path(vf.path).exists():
        raise AppException(404, "AUDIO_NOT_FOUND", "参考音频不存在")

    custom_speaker_id = _safe_doubao_speaker_id(data.custom_speaker_id or f"voice_studio_{voice.voice_id}")
    speaker_id = _safe_doubao_speaker_id(data.speaker_id or custom_speaker_id)
    demo_text = (data.demo_text or voice.reference_text or "这是豆包云端音色训练试听。").strip()[:300]
    try:
        train_response = doubao_client.train_voice_clone(
            base_url=settings.doubao_base_url,
            api_key=api_key,
            resource_id=settings.doubao_default_icl_resource_id,
            speaker_id=speaker_id,
            custom_speaker_id=custom_speaker_id,
            audio_path=vf.path,
            text=voice.reference_text,
            language=data.language or voice.default_language or "zh",
            demo_text=demo_text,
            enable_audio_denoise=data.enable_audio_denoise,
            disable_volume_normalization=data.disable_volume_normalization,
        )
        try:
            status_response = doubao_client.get_voice(
                base_url=settings.doubao_base_url,
                api_key=api_key,
                resource_id=settings.doubao_default_icl_resource_id,
                speaker_id=speaker_id,
                custom_speaker_id=custom_speaker_id,
            )
            summary = doubao_client.summarize_voice_status(status_response, speaker_id=speaker_id)
        except doubao_client.DoubaoAPIError:
            summary = {
                "speaker_id": doubao_client.masked_identifier(speaker_id),
                "status": "submitted",
                "request_id": train_response.request_id,
                "logid": train_response.logid,
            }
        updated = voice_store.update_external_binding(
            voice_id,
            provider="doubao",
            external_voice_id=speaker_id,
            status=_doubao_status_from_summary(summary),
            metadata=_doubao_metadata(summary, custom_speaker_id=custom_speaker_id),
            recommended_engine_id="doubao-tts-voiceclone",
        )
    except doubao_client.DoubaoAPIError as exc:
        raise AppException(502, "DOUBAO_VOICE_CLONE_FAILED", _doubao_error_message(exc)) from exc
    if not updated:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return DoubaoVoiceCloneResponse(voice=updated, summary=summary)


@router.post("/{voice_id}/doubao/status", response_model=DoubaoVoiceCloneResponse)
async def refresh_doubao_voice_status(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    if voice.external_provider != "doubao" or not voice.external_voice_id:
        raise AppException(400, "DOUBAO_VOICE_NOT_BOUND", "当前音色还没有豆包云端 speaker_id")
    settings = settings_store.get()
    api_key = settings_store.doubao_api_key()
    if not api_key:
        raise AppException(400, "DOUBAO_API_KEY_REQUIRED", "请先在设置中配置豆包 API Key")
    custom_speaker_id = _doubao_custom_speaker_id(voice)
    try:
        response = doubao_client.get_voice(
            base_url=settings.doubao_base_url,
            api_key=api_key,
            resource_id=settings.doubao_default_icl_resource_id,
            speaker_id=voice.external_voice_id,
            custom_speaker_id=custom_speaker_id,
        )
        summary = doubao_client.summarize_voice_status(response, speaker_id=voice.external_voice_id)
    except doubao_client.DoubaoAPIError as exc:
        raise AppException(502, "DOUBAO_VOICE_STATUS_FAILED", _doubao_error_message(exc)) from exc
    updated = voice_store.update_external_binding(
        voice_id,
        provider="doubao",
        external_voice_id=voice.external_voice_id,
        status=_doubao_status_from_summary(summary),
        metadata=_doubao_metadata(summary, custom_speaker_id=custom_speaker_id),
    )
    if not updated:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return DoubaoVoiceCloneResponse(voice=updated, summary=summary)


@router.delete("/{voice_id}/doubao/binding", response_model=VoiceAsset)
async def unbind_doubao_voice(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    updated = voice_store.clear_external_binding(voice_id, provider="doubao")
    if not updated:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return updated


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    voice_store.delete_voice(voice_id)
    return {"status": "deleted"}


@router.get("/{voice_id}/audio/{file_id}")
async def get_reference_audio(voice_id: str, file_id: str):
    voice = voice_store.get_voice(voice_id)
    vf = voice_store.get_file(file_id)
    if not voice or file_id not in voice.reference_audio_ids or not vf:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    return FileResponse(vf.path)
