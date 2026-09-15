from __future__ import annotations

import hashlib
import json

from app.domains.video_localization.schemas import VideoLocalizationDraft

PROMPT_VERSION = "semantic-tts-grouping-v1"
DEFAULT_TARGET_CHARS = 120
DEFAULT_MAX_CHARS = 180

SYSTEM_PROMPT = """把连续的中文字幕分成适合一次语音合成的组。

规则：
- 只有说话人相同、场景一致、语义连续完整的相邻字幕才能同组。
- 保持原顺序，只能合并相邻项。
- 不修改文字，不处理时间码。
- 每组尽量接近目标字数，不超过最大字数。
- 每个字幕编号必须且只能出现一次。

只返回 JSON：
{"groups":[["字幕编号1","字幕编号2"],["字幕编号3"]]}"""


def build_items(draft: VideoLocalizationDraft) -> list[dict[str, str]]:
    cues_by_id = {cue.cue_id: cue for cue in draft.cues}
    items: list[dict[str, str]] = []
    for subtitle in sorted(draft.localized_subtitles, key=lambda item: (item.start_ms, item.end_ms, item.subtitle_id)):
        source_ids = subtitle.source_cue_ids or ([subtitle.linked_cue_id] if subtitle.linked_cue_id else [])
        speakers = [
            str(cues_by_id[cue_id].speaker_id or "").strip()
            for cue_id in source_ids
            if cue_id in cues_by_id and str(cues_by_id[cue_id].speaker_id or "").strip()
        ]
        items.append(
            {
                "subtitle_id": subtitle.subtitle_id,
                "text": subtitle.text.strip(),
                "speaker_id": speakers[0] if speakers else "unknown",
            }
        )
    return items


def source_fingerprint(items: list[dict[str, str]]) -> str:
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_limits(
    target_chars: int,
    max_chars: int,
) -> tuple[int, int]:
    normalized_target = max(20, min(int(target_chars), 1000))
    normalized_max = max(
        normalized_target,
        min(int(max_chars), 2000),
    )
    return normalized_target, normalized_max


def validate_groups(raw_groups: object, items: list[dict[str, str]], max_chars: int) -> list[list[str]]:
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError("没有返回可用的字幕分组")

    ordered_ids = [item["subtitle_id"] for item in items]
    item_by_id = {item["subtitle_id"]: item for item in items}
    groups: list[list[str]] = []
    flattened: list[str] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list) or not raw_group:
            raise ValueError("分组中存在空组或无效组")
        group = [str(value).strip() for value in raw_group]
        if any(value not in item_by_id for value in group):
            raise ValueError("分组包含不存在的字幕编号")
        indexes = [ordered_ids.index(value) for value in group]
        if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            raise ValueError("每组只能包含连续字幕")
        speakers = {item_by_id[value]["speaker_id"] for value in group}
        if len(speakers) > 1:
            raise ValueError("同一组不能跨说话人")
        char_count = sum(len(item_by_id[value]["text"]) for value in group)
        if len(group) > 1 and char_count > max_chars:
            raise ValueError("分组超过最大字数")
        groups.append(group)
        flattened.extend(group)

    if flattened != ordered_ids:
        raise ValueError("字幕编号存在遗漏、重复或顺序变化")
    return groups


def build_result(
    items: list[dict[str, str]],
    groups: list[list[str]],
    *,
    target_chars: int,
    max_chars: int,
    llm_calls: list[object],
) -> dict:
    item_by_id = {item["subtitle_id"]: item for item in items}
    return {
        "version": PROMPT_VERSION,
        "source_fingerprint": source_fingerprint(items),
        "target_chars": target_chars,
        "max_chars": max_chars,
        "llm_calls": [
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in llm_calls
        ],
        "groups": [
            {
                "group_id": f"semantic_group_{index + 1:04d}",
                "subtitle_ids": group,
                "text_preview": " ".join(
                    item_by_id[value]["text"] for value in group
                )[:120],
                "char_count": sum(
                    len(item_by_id[value]["text"]) for value in group
                ),
                "speaker_id": item_by_id[group[0]]["speaker_id"],
            }
            for index, group in enumerate(groups)
        ],
    }
