from __future__ import annotations

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft


def export_srt(draft: VideoLocalizationDraft, kind: str) -> str:
    if kind not in {"en", "zh", "bilingual"}:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_KIND_UNSUPPORTED", "Subtitle kind must be en, zh, or bilingual")

    blocks: list[str] = []
    for cue in draft.cues:
        if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
            continue
        lines = _subtitle_lines_for(cue, kind)
        if not lines:
            continue
        blocks.append(
            "\n".join(
                [
                    str(len(blocks) + 1),
                    f"{_format_srt_time(cue.start_ms)} --> {_format_srt_time(cue.end_ms)}",
                    *lines,
                ]
            )
        )
    if not blocks:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLES_EMPTY", "No timed subtitle cues are available")
    return "\n\n".join(blocks) + "\n"


def _subtitle_lines_for(cue: VideoLocalizationCue, kind: str) -> list[str]:
    english = (cue.en_subtitle_text or "").strip()
    chinese = (cue.zh_localized_subtitle_text or "").strip()
    if kind == "en":
        return [english] if english else []
    if kind == "zh":
        return [chinese] if chinese else []
    return [line for line in [english, chinese] if line]


def _format_srt_time(value_ms: int) -> str:
    total_ms = max(0, int(value_ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
