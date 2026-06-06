from fastapi import APIRouter, HTTPException, UploadFile, File
"""声音资产库 API"""

from app.models.exceptions import AppException

from app.models.schemas import VoiceAsset, VoiceAssetCreate
from app.services import voice_store

router = APIRouter()


@router.get("", response_model=list[VoiceAsset])
async def list_voices():
    return voice_store.list_voices()


@router.post("", response_model=VoiceAsset)
async def create_voice(data: VoiceAssetCreate):
    return voice_store.create_voice(data)


@router.get("/{voice_id}", response_model=VoiceAsset)
async def get_voice(voice_id: str):
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.patch("/{voice_id}", response_model=VoiceAsset)
async def update_voice(voice_id: str, data: VoiceAssetCreate):
    voice = voice_store.update_voice(voice_id, data)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    return voice


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    voice_store.delete_voice(voice_id)
    return {"status": "deleted"}


@router.post("/upload")
async def upload_reference_audio(file: UploadFile = File(...)):
    """上传参考音频，返回 file_id 和质量检测结果"""
    file_id = await voice_store.upload_audio(file)

    # 质量检测
    from app.services.audio_quality import analyze_audio
    import os
    voice_dir = os.path.expanduser("~/VoiceStudio/voices")
    audio_path = None
    for ext in [".wav", ".mp3", ".flac", ".ogg"]:
        candidate = os.path.join(voice_dir, f"{file_id}{ext}")
        if os.path.exists(candidate):
            audio_path = candidate
            break

    quality = None
    if audio_path:
        quality = analyze_audio(audio_path)

    return {
        "file_id": file_id,
        "filename": file.filename,
        "quality": quality,
    }


@router.post("/{voice_id}/test-generate")
async def test_generate_voice(voice_id: str):
    """用该声音生成测试语音"""
    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    if not voice.reference_audio_ids:
        raise AppException(400, "INVALID_REQUEST", "该声音没有参考音频")
    # 提交一个测试生成任务
    from app.services.task_queue import submit
    from app.models.schemas import GenerateRequest
    task_id = await submit(GenerateRequest(
        text="这是一段测试语音。",
        engine_id="indextts",
        engine_version="v2",
        voice_id=voice_id,
    ))
    return {"task_id": task_id, "status": "queued"}

@router.get("/{voice_id}/audio/{audio_id}")
async def get_reference_audio(voice_id: str, audio_id: str):
    """提供参考音频文件"""
    import os

    voice = voice_store.get_voice(voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    if audio_id not in voice.reference_audio_ids:
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found in voice")

    voice_dir = os.path.expanduser("~/VoiceStudio/voices")
    for ext in [".wav", ".mp3", ".flac", ".ogg"]:
        path = os.path.join(voice_dir, f"{audio_id}{ext}")
        if os.path.exists(path):
            from fastapi.responses import FileResponse
            return FileResponse(path, media_type=f"audio/{ext[1:]}")
    raise AppException(404, "AUDIO_FILE_NOT_FOUND", "Audio file not found on disk")
