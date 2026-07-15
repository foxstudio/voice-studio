from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.errors import AppException
from app.schemas.voice_studio import LicenseStatus, VoiceAsset, VoiceAssetCreate, VoiceAssetUpdate, VoiceEngineBinding, VoiceFile, now_iso
from app.services import audio_tools, custom_reference_store, database as db, settings_store, voice_aliases


MAX_REFERENCE_MEDIA_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def list_voices(offset: int = 0, limit: int = 100) -> list[VoiceAsset]:
    all_voices = [_normalize_voice(VoiceAsset(**d)) for d in db.list_all("voices", "updated_at", limit=-1)]
    return all_voices[offset:offset + limit]


def get_voice(voice_id: str) -> VoiceAsset | None:
    data = db.get_one("voices", "voice_id", voice_id)
    return _normalize_voice(VoiceAsset(**data)) if data else None


def _normalize_voice(voice: VoiceAsset) -> VoiceAsset:
    normalized_name = voice_aliases.normalized_seed_voice_name(voice.name, voice.tags)
    if normalized_name != voice.name:
        voice.name = normalized_name
    voice.engine_bindings = _engine_bindings(voice)
    return voice


def save_voice(voice: VoiceAsset) -> VoiceAsset:
    voice.updated_at = now_iso()
    db.upsert("voices", voice.voice_id, voice.model_dump(exclude={"engine_bindings"}))
    voice.engine_bindings = _engine_bindings(voice)
    return voice


def create_voice(data: VoiceAssetCreate) -> VoiceAsset:
    for file_id in data.reference_audio_ids:
        custom_reference_store.promote_voice_file(file_id)
    return save_voice(VoiceAsset(**data.model_dump()))


def update_voice(voice_id: str, data: VoiceAssetUpdate) -> VoiceAsset | None:
    old = get_voice(voice_id)
    if not old:
        return None
    if data.reference_audio_ids is not None:
        for file_id in data.reference_audio_ids:
            custom_reference_store.promote_voice_file(file_id)
    merged = old.model_dump()
    merged.update(data.model_dump(exclude_unset=True))
    merged["voice_id"] = voice_id
    return save_voice(VoiceAsset(**merged))


def update_external_binding(
    voice_id: str,
    *,
    provider: str,
    external_voice_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    recommended_engine_id: str | None = None,
) -> VoiceAsset | None:
    old = get_voice(voice_id)
    if not old:
        return None
    old.external_provider = provider
    old.external_voice_id = external_voice_id
    old.external_status = status
    old.external_metadata = metadata or {}
    if recommended_engine_id:
        old.recommended_engine_id = recommended_engine_id
    return save_voice(old)


def clear_external_binding(voice_id: str, *, provider: str | None = None) -> VoiceAsset | None:
    old = get_voice(voice_id)
    if not old:
        return None
    if provider and old.external_provider != provider:
        return old
    old.external_provider = None
    old.external_voice_id = None
    old.external_status = None
    old.external_metadata = {}
    if old.recommended_engine_id == "doubao-tts-voiceclone":
        old.recommended_engine_id = None
    return save_voice(old)


def delete_voice(voice_id: str) -> None:
    voice = get_voice(voice_id)
    if voice:
        other_references = {
            file_id
            for other in list_voices(offset=0, limit=100000)
            if other.voice_id != voice_id
            for file_id in other.reference_audio_ids
        }
        voice_dir = settings_store.voice_dir().resolve()
        for file_id in voice.reference_audio_ids:
            if file_id in other_references:
                continue
            vf = get_file(file_id)
            if vf:
                file_path = Path(vf.path).resolve()
                try:
                    file_path.relative_to(voice_dir)
                except ValueError:
                    pass
                else:
                    file_path.unlink(missing_ok=True)
                    if source_media_path := custom_reference_store.owned_source_media_path(vf):
                        try:
                            source_media_path.relative_to(voice_dir)
                        except ValueError:
                            pass
                        else:
                            source_media_path.unlink(missing_ok=True)
                db.delete_one("voice_files", "file_id", file_id)
    db.delete_one("voices", "voice_id", voice_id)


def get_file(file_id: str) -> VoiceFile | None:
    data = db.get_one("voice_files", "file_id", file_id)
    return VoiceFile(**data) if data else None


def delete_file(file_id: str, *, unlink: bool = True) -> None:
    vf = get_file(file_id)
    if vf and unlink and vf.path:
        Path(vf.path).unlink(missing_ok=True)
        if source_media_path := custom_reference_store.owned_source_media_path(vf):
            source_media_path.unlink(missing_ok=True)
    db.delete_one("voice_files", "file_id", file_id)


async def upload_audio(file: UploadFile) -> dict:
    settings_store.ensure_directories()
    original_name = Path(file.filename or "voice.wav").name or "voice.wav"
    suffix = Path(original_name).suffix or ".wav"
    is_video = audio_tools.is_reference_video(original_name)
    vf = VoiceFile(original_name=original_name, path="")
    path: Path | None = None
    source_path: Path | None = None
    try:
        if is_video:
            source_path = custom_reference_store.allocate_path(vf.file_id, suffix)
            path = custom_reference_store.allocate_path(vf.file_id, ".wav")
            await _save_upload(file, source_path)
            meta = audio_tools.extract_reference_audio(source_path, path)
            vf.original_name = f"{Path(original_name).stem or 'reference'}_extracted.wav"
            vf.source_media_path = str(source_path)
            vf.source_media_name = original_name
            vf.mime_type = "audio/wav"
        else:
            path = custom_reference_store.allocate_path(vf.file_id, suffix)
            await _save_upload(file, path)
            meta = audio_tools.probe_audio(path)
            vf.mime_type = file.content_type or "audio/wav"
        vf.path = str(path)
        vf.size_bytes = meta["size_bytes"]
        vf.duration_ms = meta["duration_ms"]
        vf.sample_rate = meta["sample_rate"]
        quality = _quality_for_voice_file(vf.duration_ms)
    except AppException:
        if path:
            path.unlink(missing_ok=True)
        if source_path:
            source_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if path:
            path.unlink(missing_ok=True)
        if source_path:
            source_path.unlink(missing_ok=True)
        code = str(exc) or "REFERENCE_MEDIA_UNREADABLE"
        if code == "REFERENCE_VIDEO_NO_AUDIO":
            raise AppException(400, code, "视频没有可用的音轨，不能作为参考音色") from exc
        if code == "REFERENCE_VIDEO_FFMPEG_MISSING":
            raise AppException(500, code, "本机缺少 FFmpeg，无法从视频抽取音频") from exc
        if code.startswith("REFERENCE_VIDEO_"):
            raise AppException(400, code, "视频音频抽取失败，请换一个带声音的视频后重试") from exc
        if is_video:
            raise AppException(400, "REFERENCE_VIDEO_AUDIO_EXTRACT_FAILED", "视频音频抽取失败，请换一个带声音的视频后重试") from exc
        raise AppException(400, "REFERENCE_AUDIO_UNREADABLE", "无法读取参考音频，请换一个可播放的音频或视频后重试") from exc
    db.upsert("voice_files", vf.file_id, vf.model_dump())
    return {
        "file_id": vf.file_id,
        "filename": vf.original_name,
        "path": vf.path,
        "quality": quality,
        "duration_ms": vf.duration_ms,
        "size_bytes": vf.size_bytes,
        "source_kind": "video" if is_video else "audio",
        "source_filename": vf.source_media_name,
    }


async def _save_upload(file: UploadFile, destination: Path) -> int:
    """Save an upload in bounded chunks so a large video is never duplicated in RAM."""

    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_REFERENCE_MEDIA_BYTES:
                    raise AppException(413, "REFERENCE_MEDIA_TOO_LARGE", "参考音频或视频不能超过 200MB")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return total


def create_audio_clip(file_id: str, start_ms: int, end_ms: int) -> dict:
    settings_store.ensure_directories()
    source = get_file(file_id)
    if not source or not source.path or not Path(source.path).exists():
        raise AppException(404, "AUDIO_NOT_FOUND", "Audio not found")
    if start_ms < 0 or end_ms <= start_ms:
        raise AppException(400, "INVALID_CLIP_RANGE", "裁切出点必须大于入点")

    source_path = Path(source.path)
    try:
        source_meta = audio_tools.probe_audio(source_path)
    except Exception as exc:
        raise AppException(400, "SOURCE_AUDIO_UNREADABLE", f"无法读取原始音频: {exc}") from exc

    duration_ms = int(source_meta.get("duration_ms") or 0)
    if duration_ms <= 0:
        raise AppException(400, "SOURCE_AUDIO_UNREADABLE", "原始音频时长无效")
    start_ms = min(start_ms, max(0, duration_ms - 1))
    end_ms = min(end_ms, duration_ms)
    if end_ms - start_ms < 100:
        raise AppException(400, "CLIP_TOO_SHORT", "裁切选区不能短于 0.1 秒")

    stem = Path(source.original_name or source_path.name).stem or "reference"
    vf = VoiceFile(original_name=f"{stem}_clip_{start_ms}-{end_ms}ms.wav", path="")
    path = custom_reference_store.allocate_path(vf.file_id, ".wav")
    try:
        audio_tools.crop_file(source_path, path, start_ms, end_ms, "wav")
        meta = audio_tools.probe_audio(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise AppException(400, "AUDIO_CLIP_FAILED", f"音频裁切失败: {exc}") from exc

    vf.path = str(path)
    vf.mime_type = "audio/wav"
    vf.size_bytes = meta["size_bytes"]
    vf.duration_ms = meta["duration_ms"]
    vf.sample_rate = meta["sample_rate"]
    quality = _quality_for_voice_file(vf.duration_ms)
    db.upsert("voice_files", vf.file_id, vf.model_dump())
    return {"file_id": vf.file_id, "filename": vf.original_name, "path": vf.path, "quality": quality, "voice_file": vf}


def reference_path(voice_id: str | None) -> str | None:
    if not voice_id:
        return None
    voice = get_voice(voice_id)
    if not voice or not voice.reference_audio_ids:
        return None
    vf = get_file(voice.reference_audio_ids[0])
    return vf.path if vf else None


def _quality_for_voice_file(duration_ms: int | None) -> dict:
    quality = {"passed": True, "warnings": []}
    if duration_ms is None:
        return quality
    if duration_ms < 2000:
        quality["passed"] = False
        quality["warnings"].append("参考音频短于 2 秒")
    if duration_ms > 30000:
        quality["warnings"].append("参考音频超过 30 秒，可能影响推理速度")
    return quality


def _engine_bindings(voice: VoiceAsset) -> list[VoiceEngineBinding]:
    ref = _primary_file(voice)
    has_ref = ref is not None and Path(ref.path).exists()
    cloud_allowed = voice.license_status in [LicenseStatus.self_voice, LicenseStatus.authorized, LicenseStatus.company_authorized]
    clone_reason = ""
    if not has_ref:
        clone_reason = "缺少参考音频"
    elif not cloud_allowed:
        clone_reason = "授权状态不允许上传云端"
    elif Path(ref.path).suffix.lower() not in [".wav", ".mp3"]:
        clone_reason = "MiMo voiceclone 仅支持 wav/mp3"
    elif ref.size_bytes > 10 * 1024 * 1024:
        clone_reason = "参考音频超过 MiMo 10MB 限制"
    doubao_train_reason = ""
    if not has_ref:
        doubao_train_reason = "缺少参考音频"
    elif not cloud_allowed:
        doubao_train_reason = "授权状态不允许上传豆包云端"
    elif Path(ref.path).suffix.lower() not in [".wav", ".mp3", ".ogg", ".m4a", ".aac", ".pcm"]:
        doubao_train_reason = "豆包音色训练支持 wav/mp3/ogg/m4a/aac/pcm"
    elif ref.size_bytes > 10 * 1024 * 1024:
        doubao_train_reason = "参考音频超过豆包 10MB 限制"
    doubao_status = str(voice.external_status or "").strip().lower()
    doubao_voice_ready = (
        voice.external_provider == "doubao"
        and bool(voice.external_voice_id)
        and doubao_status in {"success", "active", "available", "passed", "2", "3"}
    )
    doubao_voice_reason = ""
    if voice.external_provider == "doubao" and voice.external_voice_id and not doubao_voice_ready:
        doubao_voice_reason = f"豆包云端音色状态：{voice.external_status or '未知'}"
    elif not voice.external_voice_id:
        doubao_voice_reason = "尚未训练豆包云端音色"
    local_clone_reason = ""
    if not has_ref:
        local_clone_reason = "缺少参考音频"
    elif not voice.reference_text.strip():
        local_clone_reason = "缺少参考台词"

    return [
        VoiceEngineBinding(
            engine_id="indextts-v2",
            mode="reference_audio",
            available=has_ref,
            reason="" if has_ref else "IndexTTS v2 需要参考音频",
        ),
        VoiceEngineBinding(
            engine_id="omnivoice",
            mode="reference_audio",
            available=has_ref,
            reason="" if has_ref else "OmniVoice 可无参考音频改用声音设计",
        ),
        VoiceEngineBinding(
            engine_id="f5-tts",
            mode="voice_clone",
            available=has_ref and not local_clone_reason,
            reason=local_clone_reason,
        ),
        VoiceEngineBinding(
            engine_id="cosyvoice-zero-shot",
            mode="voice_clone",
            available=has_ref and not local_clone_reason,
            reason=local_clone_reason,
        ),
        VoiceEngineBinding(
            engine_id="mimo-v2.5-tts-preset",
            mode="preset_voice",
            available=False,
            reason="MiMo 预置音色来自官方云端音色目录，不使用本地参考音频",
        ),
        VoiceEngineBinding(
            engine_id="mimo-v2.5-tts-voiceclone",
            mode="voice_clone",
            available=has_ref and cloud_allowed and not clone_reason,
            reason=clone_reason,
        ),
        VoiceEngineBinding(
            engine_id="doubao-voice-clone-train",
            mode="voice_clone",
            available=has_ref and cloud_allowed and not doubao_train_reason,
            reason=doubao_train_reason,
        ),
        VoiceEngineBinding(
            engine_id="doubao-tts-voiceclone",
            mode="voice_clone",
            available=doubao_voice_ready,
            reason="" if doubao_voice_ready else doubao_voice_reason,
            external_voice_id=voice.external_voice_id,
            parameters=voice.external_metadata if voice.external_provider == "doubao" else {},
        ),
    ]


def _primary_file(voice: VoiceAsset) -> VoiceFile | None:
    if not voice.reference_audio_ids:
        return None
    return get_file(voice.reference_audio_ids[0])
