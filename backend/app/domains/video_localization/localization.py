from __future__ import annotations

from app.domains.video_localization import cues as cue_tools
from app.errors import AppException
from app.schemas.voice_studio import VideoLocalizationCue, VideoLocalizationDraft
from app.services import text_normalizer


def with_chinese_draft(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    if not draft.cues:
        raise AppException(400, "VIDEO_LOCALIZATION_CUES_MISSING", "Create English ASR cues before generating Chinese localization draft")

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
            zh_text = f"【待本土化】{source_text}"
            patch["zh_localized_subtitle_text"] = zh_text
            flags = cue_tools.add_flags(flags, ["localization_draft", "needs_human_localization"])
            changed = True

        if not (cue.tts_recommended_text or "").strip():
            patch["tts_recommended_text"] = text_normalizer.normalize_spoken_numbers(zh_text)
            flags = cue_tools.add_flags(flags, ["tts_text_normalized"])
            changed = True

        if patch:
            patch["quality_flags"] = flags
            next_cues.append(cue.model_copy(update=patch))
        else:
            next_cues.append(cue)

    if not changed:
        raise AppException(400, "VIDEO_LOCALIZATION_LOCALIZATION_UNCHANGED", "All cues already have Chinese subtitle and TTS text")
    return draft.model_copy(update={"cues": next_cues})
