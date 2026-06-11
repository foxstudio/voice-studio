from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.schemas.voice_studio import LicenseStatus, VoiceAsset, VoiceAssetCreate, VoiceAssetUpdate, VoiceEngineBinding, VoiceFile, now_iso
from app.services import audio_tools, database as db, settings_store, voice_aliases


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
    return save_voice(VoiceAsset(**data.model_dump()))


def update_voice(voice_id: str, data: VoiceAssetUpdate) -> VoiceAsset | None:
    old = get_voice(voice_id)
    if not old:
        return None
    merged = old.model_dump()
    merged.update(data.model_dump(exclude_unset=True))
    merged["voice_id"] = voice_id
    return save_voice(VoiceAsset(**merged))


def delete_voice(voice_id: str) -> None:
    voice = get_voice(voice_id)
    if voice:
        voice_dir = settings_store.voice_dir().resolve()
        for file_id in voice.reference_audio_ids:
            vf = get_file(file_id)
            if vf:
                file_path = Path(vf.path).resolve()
                try:
                    file_path.relative_to(voice_dir)
                except ValueError:
                    pass
                else:
                    file_path.unlink(missing_ok=True)
                db.delete_one("voice_files", "file_id", file_id)
    db.delete_one("voices", "voice_id", voice_id)


def get_file(file_id: str) -> VoiceFile | None:
    data = db.get_one("voice_files", "file_id", file_id)
    return VoiceFile(**data) if data else None


async def upload_audio(file: UploadFile) -> dict:
    settings_store.ensure_directories()
    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    vf = VoiceFile(original_name=file.filename or "voice.wav", path="")
    path = settings_store.voice_dir() / f"{vf.file_id}{suffix.lower()}"
    content = await file.read()
    path.write_bytes(content)
    vf.path = str(path)
    vf.mime_type = file.content_type or "audio/wav"
    vf.size_bytes = len(content)
    quality = {"passed": True, "warnings": []}
    try:
        meta = audio_tools.probe_audio(path)
        vf.duration_ms = meta["duration_ms"]
        vf.sample_rate = meta["sample_rate"]
        if vf.duration_ms < 2000:
            quality["passed"] = False
            quality["warnings"].append("参考音频短于 2 秒")
        if vf.duration_ms > 30000:
            quality["warnings"].append("参考音频超过 30 秒，可能影响推理速度")
    except Exception as exc:
        quality = {"passed": False, "warnings": [f"无法读取音频: {exc}"]}
    db.upsert("voice_files", vf.file_id, vf.model_dump())
    return {"file_id": vf.file_id, "filename": vf.original_name, "quality": quality}


def reference_path(voice_id: str | None) -> str | None:
    if not voice_id:
        return None
    voice = get_voice(voice_id)
    if not voice or not voice.reference_audio_ids:
        return None
    vf = get_file(voice.reference_audio_ids[0])
    return vf.path if vf else None


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
    ]


def _primary_file(voice: VoiceAsset) -> VoiceFile | None:
    if not voice.reference_audio_ids:
        return None
    return get_file(voice.reference_audio_ids[0])
