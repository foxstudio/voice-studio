from __future__ import annotations

import re


SEED_VOICE_NAMES: dict[str, str] = {
    "index_voice_01": "官方女声候选 - 清晰旁白",
    "index_voice_02": "官方男声候选 - 稳定讲解",
    "index_voice_03": "官方女声候选 - 柔和讲述",
    "index_voice_04": "官方男声候选 - 沉稳叙事",
    "index_voice_05": "官方旁白候选 - 知识讲解",
    "index_voice_06": "官方播报候选 - 清楚播报",
    "index_voice_07": "官方情绪候选 - 角色表达",
    "index_voice_08": "官方角色候选 - 对白配音",
    "index_voice_09": "官方女声候选 - 自然口播",
    "index_voice_11": "官方男声候选 - 口播解说",
    "index_voice_12": "官方强情绪候选 - 强调表达",
    "index_emo_sad": "官方悲伤情绪参考",
}


def seed_label(seed_id: str) -> str | None:
    return SEED_VOICE_NAMES.get(seed_id)


def normalized_seed_voice_name(name: str, tags: list[str]) -> str:
    """Only rename old generic official seed names; user-edited names stay intact."""
    seed_id = next((tag.removeprefix("seed:") for tag in tags if tag.startswith("seed:")), "")
    label = seed_label(seed_id)
    if not label:
        return name
    if name == label:
        return name
    if re.fullmatch(r"IndexTTS 官方参考音色 \d{2}", name):
        return label
    if name == "IndexTTS 官方悲伤情绪参考":
        return label
    return name
