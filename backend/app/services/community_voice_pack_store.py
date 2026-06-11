from __future__ import annotations

import urllib.request
from urllib.parse import urlencode
from pathlib import Path

from app.schemas.voice_studio import (
    AudioQualityResult,
    CommunityVoiceCandidate,
    CommunityVoicePack,
    LicenseStatus,
    VoiceAssetCreate,
    VoiceFile,
    VoiceType,
)
from app.services import audio_tools, database as db, settings_store, voice_store

HF_RESOLVE = "https://huggingface.co"
CSEMOTIONS_ROWS_API = "https://datasets-server.huggingface.co/rows"


def _hf(repo: str, path: str) -> str:
    return f"{HF_RESOLVE}/{repo}/resolve/main/{path}"


def _csemotions_row(row_idx: int) -> str:
    return f"hf-dataset-row://AIDC-AI/CSEMOTIONS/default/train/{row_idx}"


def _candidate(
    candidate_id: str,
    name: str,
    source: str,
    download_url: str,
    *,
    description: str = "",
    tags: list[str] | None = None,
    license_status: LicenseStatus = LicenseStatus.authorized,
    reference_text: str = "",
) -> CommunityVoiceCandidate:
    return CommunityVoiceCandidate(
        candidate_id=candidate_id,
        name=name,
        description=description,
        source=source,
        download_url=download_url,
        reference_text=reference_text,
        tags=tags or [],
        license_status=license_status,
    )


CSEMOTIONS_SAMPLES = [
    (
        1200,
        "female004",
        "angry",
        "强势争执女声",
        "短句爆发、压迫感强，适合争吵、反派质问、强情绪对白。",
        "你怎么这么自私！给我走开！",
        ["女声", "愤怒", "爆发", "对白"],
    ),
    (
        320,
        "female001",
        "surprise",
        "讽刺喜剧女声",
        "带讽刺和夸张感，适合轻喜剧、吐槽和反转台词。",
        "你的方案真是别出心裁，连甲方都感动到想连夜跑路。",
        ["女声", "惊讶", "讽刺", "喜剧"],
    ),
    (
        1000,
        "female003",
        "playfulness",
        "俏皮吐槽女声",
        "节奏灵动，带调侃感，适合动画旁白、轻松角色、段子式台词。",
        "您说“钱不是省出来的”，但月光的姿态确实很洒脱。",
        ["女声", "玩闹", "俏皮", "吐槽"],
    ),
    (
        800,
        "female003",
        "fearful",
        "悬疑低语女声",
        "紧张、克制、画面感强，适合悬疑叙事和惊悚角色。",
        "图书馆旧书架后，传来隐隐哭声，透着无尽哀怨。",
        ["女声", "恐惧", "悬疑", "低语感"],
    ),
    (
        240,
        "female001",
        "sad",
        "伤感独白女声",
        "情绪下沉、叙述感强，适合回忆、离别和内心独白。",
        "我试图在喧嚣中寻找快乐，但每个角落都藏着你曾留下的痕迹，无法抹去。",
        ["女声", "悲伤", "独白", "叙事"],
    ),
    (
        1800,
        "female005",
        "happy",
        "明亮元气女声",
        "明亮、开放、正向，适合动画活泼角色和轻松介绍。",
        "在美丽的秋日里，金黄的树叶在微风中飘落，仿佛大自然为我们铺上了一条快乐的地毯。",
        ["女声", "开心", "元气", "动画感"],
    ),
    (
        3800,
        "male005",
        "angry",
        "低沉压迫男声",
        "低沉、愤怒、压迫感强，适合硬派角色、冲突对白和黑色叙事。",
        "我真是受够了你的无理要求，为什么你总是要把所有责任推给我！",
        ["男声", "愤怒", "低沉", "压迫感"],
    ),
    (
        3400,
        "male004",
        "fearful",
        "惊悚叙事男声",
        "紧张、阴冷、戏剧感强，适合恐怖故事和悬疑片段。",
        "深夜的停车场，车的后视镜里突然出现一张扭曲的脸。",
        ["男声", "恐惧", "惊悚", "叙事"],
    ),
    (
        2200,
        "male001",
        "happy",
        "温暖正剧男声",
        "温和、稳定、带希望感，适合正剧旁白和温情角色。",
        "清晨的阳光透过窗帘洒在床上，让我感受到温暖与希望，开启美好的一天。",
        ["男声", "开心", "温暖", "正剧"],
    ),
    (
        4100,
        "male005",
        "sad",
        "沧桑独白男声",
        "低落、沉重、故事感强，适合江湖感独白和失落叙事。",
        "我站在旧时光里，等了一场不会再来的归途，看着熟悉的路口，听着风吹过树梢的声音。",
        ["男声", "悲伤", "沧桑", "独白"],
    ),
    (
        3000,
        "male003",
        "fearful",
        "冒险悬疑男声",
        "气氛紧绷、叙事张力强，适合探险、谜案、紧急场景。",
        "拉开帐篷拉链，发现营地周围的草地上布满了巨大的脚印，而同行的伙伴们都消失不见。",
        ["男声", "恐惧", "冒险", "悬疑"],
    ),
    (
        2600,
        "male002",
        "happy",
        "爽朗轻喜男声",
        "活跃、轻松、外放，适合喜剧化旁白和活力角色。",
        "参加派对时，欢乐的音乐与舞蹈仿佛让整个世界都充满了活力，令人难以忘怀。",
        ["男声", "开心", "爽朗", "轻喜剧"],
    ),
]


PACKS: list[CommunityVoicePack] = [
    CommunityVoicePack(
        pack_id="csemotions_character_samples",
        name="CSEMOTIONS 中文角色感精选",
        description="来自 CSEMOTIONS 的专业录音棚中文情绪语音，按角色风格精选少量高表现力参考音频。",
        source="AIDC-AI/CSEMOTIONS",
        license_summary="CSEMOTIONS / Apache 2.0。专业配音演员录制，非名人/影视角色复刻。",
        tags=["社区", "中文", "角色感", "情绪", "Apache 2.0"],
        candidates=[
            _candidate(
                f"csemotions_{row_idx}",
                name,
                "AIDC-AI/CSEMOTIONS",
                _csemotions_row(row_idx),
                description=f"{description} 原始 speaker={speaker}，emotion={emotion}。",
                tags=["社区音色", "CSEMOTIONS", "中文", "角色感", *tags, "Apache 2.0"],
                license_status=LicenseStatus.authorized,
                reference_text=reference_text,
            )
            for row_idx, speaker, emotion, name, description, reference_text, tags in CSEMOTIONS_SAMPLES
        ],
    ),
]


def list_packs() -> list[CommunityVoicePack]:
    return [_mark_pack_imported(pack.model_copy(deep=True)) for pack in PACKS]


def get_pack(pack_id: str) -> CommunityVoicePack | None:
    pack = next((pack for pack in PACKS if pack.pack_id == pack_id), None)
    return _mark_pack_imported(pack.model_copy(deep=True)) if pack else None


def import_pack(pack_id: str, candidate_ids: list[str] | None = None) -> CommunityVoicePack:
    pack = next((pack for pack in PACKS if pack.pack_id == pack_id), None)
    if not pack:
        raise ValueError("COMMUNITY_VOICE_PACK_NOT_FOUND")
    selected = set(candidate_ids or [])
    if selected:
        missing = selected - {candidate.candidate_id for candidate in pack.candidates}
        if missing:
            raise ValueError("COMMUNITY_VOICE_CANDIDATE_NOT_FOUND")

    for candidate in pack.candidates:
        if selected and candidate.candidate_id not in selected:
            continue
        _import_candidate(pack, candidate)
    return _mark_pack_imported(pack.model_copy(deep=True))


def _mark_pack_imported(pack: CommunityVoicePack) -> CommunityVoicePack:
    imported_count = 0
    for candidate in pack.candidates:
        imported = _find_imported(candidate)
        candidate.imported_voice_id = imported.imported_voice_id
        candidate.quality = imported.quality
        if candidate.imported_voice_id:
            imported_count += 1
    pack.imported_count = imported_count
    return pack


def _find_imported(candidate: CommunityVoiceCandidate) -> CommunityVoiceCandidate:
    marker = f"community:{candidate.candidate_id}"
    for voice in voice_store.list_voices():
        if marker not in voice.tags:
            continue
        candidate.imported_voice_id = voice.voice_id
        if voice.reference_audio_ids:
            vf = voice_store.get_file(voice.reference_audio_ids[0])
            if vf and Path(vf.path).exists():
                candidate.quality = AudioQualityResult(**audio_tools.quality_metrics(vf.path))
        break
    return candidate


def _import_candidate(pack: CommunityVoicePack, candidate: CommunityVoiceCandidate) -> CommunityVoiceCandidate:
    existing = _find_imported(candidate.model_copy(deep=True))
    if existing.imported_voice_id:
        return existing

    settings_store.ensure_directories()
    suffix = Path(candidate.download_url).suffix or ".wav"
    vf = VoiceFile(original_name=f"{candidate.candidate_id}{suffix}", path="")
    path = settings_store.voice_dir() / f"{vf.file_id}{suffix.lower()}"
    _download_audio(candidate.download_url, path)

    vf.path = str(path)
    vf.mime_type = "audio/wav"
    vf.size_bytes = path.stat().st_size
    meta = audio_tools.probe_audio(path)
    vf.duration_ms = meta["duration_ms"]
    vf.sample_rate = meta["sample_rate"]
    db.upsert("voice_files", vf.file_id, vf.model_dump())

    voice = voice_store.create_voice(
        VoiceAssetCreate(
            name=candidate.name,
            voice_type=VoiceType.test_sample,
            description=f"{candidate.description} 来源：{candidate.source}。授权：{pack.license_summary}",
            default_language="en" if "英文" in candidate.tags else "zh",
            tags=[*candidate.tags, f"pack:{pack.pack_id}", f"community:{candidate.candidate_id}"],
            reference_text=candidate.reference_text,
            recommended_engine_id=candidate.recommended_engine_id,
            reference_audio_ids=[vf.file_id],
            license_status=candidate.license_status,
        )
    )
    imported = candidate.model_copy(deep=True)
    imported.imported_voice_id = voice.voice_id
    imported.quality = AudioQualityResult(**audio_tools.quality_metrics(path))
    return imported


def _download_audio(download_url: str, path: Path) -> None:
    url = _resolve_download_url(download_url)
    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=90) as client:
            response = client.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)
    except Exception:
        with urllib.request.urlopen(url, timeout=90) as response:
            path.write_bytes(response.read())


def _resolve_download_url(download_url: str) -> str:
    if not download_url.startswith("hf-dataset-row://"):
        return download_url
    parts = download_url.replace("hf-dataset-row://", "", 1).split("/")
    if len(parts) != 5:
        raise ValueError("INVALID_HF_DATASET_ROW_URL")
    dataset = "/".join(parts[:2])
    config, split, row_idx = parts[2], parts[3], int(parts[4])
    query = urlencode({"dataset": dataset, "config": config, "split": split, "offset": row_idx, "length": 1})
    with urllib.request.urlopen(f"{CSEMOTIONS_ROWS_API}?{query}", timeout=30) as response:
        data = __import__("json").loads(response.read().decode("utf-8"))
    rows = data.get("rows") or []
    if not rows:
        raise ValueError("HF_DATASET_ROW_NOT_FOUND")
    audio = rows[0].get("row", {}).get("audio")
    if isinstance(audio, list) and audio and audio[0].get("src"):
        return audio[0]["src"]
    raise ValueError("HF_DATASET_ROW_AUDIO_NOT_FOUND")
