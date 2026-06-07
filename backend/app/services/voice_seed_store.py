from __future__ import annotations

import urllib.request
from pathlib import Path

from app.models.schemas import AudioQualityResult, LicenseStatus, VoiceAssetCreate, VoiceFile, VoiceSeed, VoiceType
from app.services import audio_tools, database as db, settings_store, voice_aliases, voice_store

INDEX_EXAMPLE_RAW = "https://media.githubusercontent.com/media/index-tts/index-tts/main/examples"


SEED_LABELS = {
    1: (voice_aliases.seed_label("index_voice_01") or "官方女声候选 - 清晰旁白", ["女声", "清晰", "旁白"]),
    2: (voice_aliases.seed_label("index_voice_02") or "官方男声候选 - 稳定讲解", ["男声", "稳定", "讲解"]),
    3: (voice_aliases.seed_label("index_voice_03") or "官方女声候选 - 柔和讲述", ["女声", "柔和", "讲述"]),
    4: (voice_aliases.seed_label("index_voice_04") or "官方男声候选 - 沉稳叙事", ["男声", "沉稳", "叙事"]),
    5: (voice_aliases.seed_label("index_voice_05") or "官方旁白候选 - 知识讲解", ["旁白", "知识讲解"]),
    6: (voice_aliases.seed_label("index_voice_06") or "官方播报候选 - 清楚播报", ["播报", "清楚"]),
    7: (voice_aliases.seed_label("index_voice_07") or "官方情绪候选 - 角色表达", ["情绪", "角色"]),
    8: (voice_aliases.seed_label("index_voice_08") or "官方角色候选 - 对白配音", ["角色", "对白"]),
    9: (voice_aliases.seed_label("index_voice_09") or "官方女声候选 - 自然口播", ["女声", "自然", "口播"]),
    11: (voice_aliases.seed_label("index_voice_11") or "官方男声候选 - 口播解说", ["男声", "口播", "解说"]),
    12: (voice_aliases.seed_label("index_voice_12") or "官方强情绪候选 - 强调表达", ["强情绪", "强调"]),
}


def _index_seed(num: int, tags: list[str]) -> VoiceSeed:
    file_name = f"voice_{num:02d}.wav"
    name, inferred_tags = SEED_LABELS[num]
    return VoiceSeed(
        seed_id=f"index_voice_{num:02d}",
        name=name,
        description="来自 IndexTTS 官方 examples 的参考音频。导入后会成为音色库里的参考声音，可用于本地声音克隆测试。",
        source="IndexTTS 官方 examples",
        download_url=f"{INDEX_EXAMPLE_RAW}/{file_name}",
        recommended_engine_id="indextts-v2",
        reference_text="官方示例参考音频，用于本地测试音色克隆。",
        tags=["官方示例", "参考声音", *inferred_tags, *tags],
        license_status=LicenseStatus.test_only,
    )


SEEDS: list[VoiceSeed] = [
    _index_seed(1, ["女声候选"]),
    _index_seed(2, ["男声候选"]),
    _index_seed(3, ["女声候选"]),
    _index_seed(4, ["男声候选"]),
    _index_seed(5, ["旁白候选"]),
    _index_seed(6, ["播报候选"]),
    _index_seed(7, ["情绪候选"]),
    _index_seed(8, ["角色候选"]),
    _index_seed(9, ["女声候选"]),
    _index_seed(11, ["男声候选"]),
    _index_seed(12, ["强情绪候选"]),
    VoiceSeed(
        seed_id="index_emo_sad",
        name=voice_aliases.seed_label("index_emo_sad") or "官方悲伤情绪参考",
        description="来自 IndexTTS 官方 examples 的情绪参考音频，可用于情绪控制测试。",
        source="IndexTTS 官方 examples",
        download_url=f"{INDEX_EXAMPLE_RAW}/emo_sad.wav",
        recommended_engine_id="indextts-v2",
        reference_text="官方悲伤情绪参考音频，用于测试情绪控制。",
        tags=["官方示例", "情绪参考", "悲伤"],
        license_status=LicenseStatus.test_only,
    ),
    VoiceSeed(
        seed_id="index_emo_hate",
        name=voice_aliases.seed_label("index_emo_hate") or "官方反感情绪参考",
        description="来自 IndexTTS 官方 examples 的情绪参考音频，可用于反感、厌恶等情绪控制测试。",
        source="IndexTTS 官方 examples",
        download_url=f"{INDEX_EXAMPLE_RAW}/emo_hate.wav",
        recommended_engine_id="indextts-v2",
        reference_text="官方反感情绪参考音频，用于测试情绪控制。",
        tags=["官方示例", "情绪参考", "反感"],
        license_status=LicenseStatus.test_only,
    ),
]


def _find_imported(seed: VoiceSeed) -> VoiceSeed:
    for voice in voice_store.list_voices():
        if f"seed:{seed.seed_id}" in voice.tags:
            seed.imported_voice_id = voice.voice_id
            if voice.reference_audio_ids:
                vf = voice_store.get_file(voice.reference_audio_ids[0])
                if vf and Path(vf.path).exists():
                    seed.quality = AudioQualityResult(**audio_tools.quality_metrics(vf.path))
            break
    return seed


def list_seeds() -> list[VoiceSeed]:
    return [_find_imported(seed.model_copy(deep=True)) for seed in SEEDS]


def get_seed(seed_id: str) -> VoiceSeed | None:
    return next((seed for seed in SEEDS if seed.seed_id == seed_id), None)


def import_seed(seed_id: str) -> VoiceSeed:
    seed = get_seed(seed_id)
    if not seed:
        raise ValueError("VOICE_SEED_NOT_FOUND")
    existing = _find_imported(seed.model_copy(deep=True))
    if existing.imported_voice_id:
        return existing

    settings_store.ensure_directories()
    suffix = Path(seed.download_url).suffix or ".wav"
    vf = VoiceFile(original_name=f"{seed.seed_id}{suffix}", path="")
    path = settings_store.voice_dir() / f"{vf.file_id}{suffix}"
    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=60) as client:
            response = client.get(seed.download_url)
            response.raise_for_status()
            path.write_bytes(response.content)
    except Exception:
        with urllib.request.urlopen(seed.download_url, timeout=60) as response:
            path.write_bytes(response.read())

    vf.path = str(path)
    vf.mime_type = "audio/wav"
    vf.size_bytes = path.stat().st_size
    meta = audio_tools.probe_audio(path)
    vf.duration_ms = meta["duration_ms"]
    vf.sample_rate = meta["sample_rate"]
    db.upsert("voice_files", vf.file_id, vf.model_dump())

    voice = voice_store.create_voice(
        VoiceAssetCreate(
            name=seed.name,
            voice_type=VoiceType.test_sample,
            description=f"{seed.description} 来源：{seed.source}",
            default_language="zh",
            tags=[*seed.tags, f"seed:{seed.seed_id}"],
            reference_text=seed.reference_text,
            recommended_engine_id=seed.recommended_engine_id,
            reference_audio_ids=[vf.file_id],
            license_status=seed.license_status,
        )
    )
    imported = seed.model_copy(deep=True)
    imported.imported_voice_id = voice.voice_id
    imported.quality = AudioQualityResult(**audio_tools.quality_metrics(path))
    return imported
