from __future__ import annotations

import re

from app.domains.video_localization import cues as cue_tools
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft
from app.services import llm_runtime, settings_store


LOCALIZATION_PROMPT_VERSION = "localization-zh-v1"
LOCALIZATION_BATCH_SIZE = 24
NUMBER_PATTERN = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:%|％)?(?!\w)")


def with_chinese_draft(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    if not draft.cues:
        raise AppException(400, "VIDEO_LOCALIZATION_CUES_MISSING", "Create English ASR cues before generating Chinese localization draft")

    localized_by_id, localization_meta = _localize_missing_cues(draft)
    changed = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        patch: dict[str, object] = {}
        flags = list(cue.quality_flags)
        zh_text = (cue.zh_localized_subtitle_text or "").strip()
        if not zh_text:
            source_text = (cue.en_subtitle_text or "").strip()
            if not source_text:
                next_cues.append(cue)
                continue
            localized = localized_by_id.get(cue.cue_id)
            if localized:
                zh_text = localized["text"]
                note = localized.get("note")
                patch["notes"] = _append_localization_note(cue.notes, note)
                flags = cue_tools.add_flags(
                    [flag for flag in flags if flag not in {"localization_draft", "llm_localization_failed"}],
                    [
                        "llm_localized",
                        "needs_human_localization",
                        "localization:L1_W0",
                        f"localization_prompt:{LOCALIZATION_PROMPT_VERSION}",
                        f"localization_model:{localization_meta['model_id']}",
                    ],
                )
            else:
                zh_text = f"【待本土化】{source_text}"
                failure_flags = ["localization_draft", "needs_human_localization"]
                if localization_meta["attempted"]:
                    failure_flags.append("llm_localization_failed")
                flags = cue_tools.add_flags(flags, failure_flags)
            patch["zh_localized_subtitle_text"] = zh_text
            changed = True

        if patch:
            patch["quality_flags"] = flags
            next_cues.append(cue.model_copy(update=patch))
        else:
            next_cues.append(cue)

    if not changed:
        raise AppException(400, "VIDEO_LOCALIZATION_LOCALIZATION_UNCHANGED", "All cues already have Chinese subtitles")
    return draft.model_copy(update={"cues": next_cues})


def _localize_missing_cues(draft: VideoLocalizationDraft) -> tuple[dict[str, dict[str, str]], dict[str, object]]:
    missing = [cue for cue in draft.cues if not (cue.zh_localized_subtitle_text or "").strip() and (cue.en_subtitle_text or "").strip()]
    profiles = settings_store.llm_profiles()
    profile = settings_store.llm_profile(profiles.default_profile_id) if profiles.default_profile_id else None
    if not missing or not profile or not profile.enabled or not profile.model_id:
        return {}, {"attempted": False, "model_id": None}

    speaker_by_id = {speaker.speaker_id: speaker for speaker in draft.speakers}
    localized: dict[str, dict[str, str]] = {}
    for start in range(0, len(missing), LOCALIZATION_BATCH_SIZE):
        batch = missing[start : start + LOCALIZATION_BATCH_SIZE]
        payload = {
            "task": LOCALIZATION_PROMPT_VERSION,
            "profile": {
                "localization_level": "L1",
                "worldview_permeability": "W0",
                "target_language": "zh-Hans",
                "audience": "中国大陆观众",
                "scene_context": draft.scene_context,
            },
            "glossary": [
                {
                    "source_text": item.source_text,
                    "corrected_source_text": item.corrected_source_text,
                    "zh_text": item.zh_text,
                    "notes": item.notes,
                }
                for item in draft.glossary
                if item.source_text.strip()
            ],
            "rules": {
                "preserve": ["facts", "numbers", "proper nouns", "causality", "negation", "speaker intent", "emotion strength", "persona"],
                "style": ["natural spoken Chinese", "concise subtitle", "minimal necessary punctuation", "no translationese"],
                "forbidden": ["timestamps", "summarization", "invented facts", "new Chinese historical or cultural entities", "internet memes"],
                "numbers": "Keep every Arabic number from the source unchanged in zh_subtitle_text.",
            },
            "cues": [
                {
                    "cue_id": cue.cue_id,
                    "source_text": cue.en_subtitle_text,
                    "speaker": _speaker_context(speaker_by_id.get(cue.speaker_id)),
                    "previous": _neighbor_text(draft.cues, cue.cue_id, -1),
                    "next": _neighbor_text(draft.cues, cue.cue_id, 1),
                }
                for cue in batch
            ],
            "output": "Return {cues:[{cue_id, zh_subtitle_text, adaptation_note}]}. Return every cue exactly once and in input order.",
        }
        try:
            raw = llm_runtime.complete_json(
                system_prompt=_localization_system_prompt(),
                user_payload=payload,
                profile_id=profile.profile_id,
                temperature=0.15,
                max_tokens=min(16384, max(4096, len(batch) * 300)),
            )
            items = raw.get("cues") if isinstance(raw, dict) else None
            if not isinstance(items, list) or [item.get("cue_id") for item in items if isinstance(item, dict)] != [cue.cue_id for cue in batch]:
                continue
            for cue, item in zip(batch, items):
                text = str(item.get("zh_subtitle_text") or "").strip()
                if not _localized_text_is_safe(cue.en_subtitle_text or "", text):
                    continue
                localized[cue.cue_id] = {
                    "text": text,
                    "note": str(item.get("adaptation_note") or "").strip()[:500],
                }
        except Exception:
            continue
    return localized, {"attempted": True, "model_id": profile.model_id}


def _localized_text_is_safe(source: str, localized: str) -> bool:
    if not localized or localized.startswith("【待本土化】"):
        return False
    source_numbers = NUMBER_PATTERN.findall(source)
    return all(number in localized for number in source_numbers)


def _speaker_context(speaker) -> dict[str, str | None] | None:
    if speaker is None:
        return None
    return {
        "speaker_id": speaker.speaker_id,
        "display_name": speaker.display_name,
        "notes": speaker.notes,
    }


def _neighbor_text(cues: list[VideoLocalizationCue], cue_id: str, offset: int) -> str | None:
    index = next((index for index, cue in enumerate(cues) if cue.cue_id == cue_id), -1)
    target = index + offset
    if index < 0 or target < 0 or target >= len(cues):
        return None
    return (cues[target].en_subtitle_text or "").strip() or None


def _append_localization_note(existing: str | None, note: str | None) -> str | None:
    note = (note or "").strip()
    if not note:
        return existing
    line = f"本土化说明：{note}"
    return f"{existing.rstrip()}\n{line}" if existing and existing.strip() else line


def _localization_system_prompt() -> str:
    return (
        "你是中文影视字幕本土化编辑。默认执行 L1 功能等值与 W0 封闭世界观：先保住源事实、说话行为、人物关系和口吻，"
        "再写成中国大陆观众自然会说、能快速读懂的口语字幕。不得逐字硬译，不得添加原作不存在的中国历史人物、典故、地名或网络热梗。"
        "数字、专名、否定、因果、比较和结论不得改变。字幕只写当前 cue 实际表达的内容，不生成或修改时间码，不合并、拆分或遗漏 cue。"
        "标点保持克制；adaptation_note 只简述必要的意译或风险，没有特殊改写时留空。只返回约定 JSON。"
    )
