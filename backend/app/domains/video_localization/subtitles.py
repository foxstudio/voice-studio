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


def import_srt(draft: VideoLocalizationDraft, kind: str, srt_text: str, *, update_timing: bool = True, overwrite_tts: bool = False) -> VideoLocalizationDraft:
    if kind not in {"en", "zh", "tts"}:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_KIND_UNSUPPORTED", "Subtitle import kind must be en, zh, or tts")
    if not draft.cues:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_NO_CUES", "No cue exists to receive imported subtitles")

    entries = _parse_srt(srt_text)
    if not entries:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_EMPTY", "No valid SRT subtitle entries were found")

    next_cues: list[VideoLocalizationCue] = []
    for index, cue in enumerate(draft.cues):
        entry = entries[index] if index < len(entries) else None
        if not entry:
            next_cues.append(cue)
            continue
        update = {}
        if update_timing:
            update["start_ms"] = entry["start_ms"]
            update["end_ms"] = entry["end_ms"]
            update["source_duration_ms"] = entry["end_ms"] - entry["start_ms"]
        if kind == "en":
            update["en_subtitle_text"] = entry["text"]
        elif kind == "tts":
            update["tts_recommended_text"] = entry["text"]
        else:
            update["zh_localized_subtitle_text"] = entry["text"]
            if overwrite_tts or not (cue.tts_recommended_text or "").strip():
                update["tts_recommended_text"] = entry["text"]
        update["quality_flags"] = sorted({flag for flag in cue.quality_flags if flag != f"{kind}_srt_import"} | {f"{kind}_srt_import"})
        next_cues.append(VideoLocalizationCue(**{**cue.model_dump(), **update}))

    return draft.model_copy(update={"cues": next_cues})


def _subtitle_lines_for(cue: VideoLocalizationCue, kind: str) -> list[str]:
    english = (cue.en_subtitle_text or "").strip()
    chinese = (cue.zh_localized_subtitle_text or "").strip()
    if kind == "en":
        return [english] if english else []
    if kind == "zh":
        return [chinese] if chinese else []
    return [line for line in [english, chinese] if line]


def _parse_srt(text: str) -> list[dict[str, int | str]]:
    entries: list[dict[str, int | str]] = []
    for block in text.replace("\r", "").split("\n\n"):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        time_line_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
        if time_line_index == -1:
            continue
        time_parts = [part.strip() for part in lines[time_line_index].split("-->")]
        if len(time_parts) < 2:
            continue
        raw_start, raw_end = time_parts[0], time_parts[1]
        start_ms = _parse_srt_time(raw_start.split()[0] if raw_start else "")
        end_ms = _parse_srt_time(raw_end.split()[0] if raw_end else "")
        subtitle_text = "\n".join(lines[time_line_index + 1 :]).strip()
        if start_ms is None or end_ms is None or end_ms <= start_ms or not subtitle_text:
            continue
        entries.append({"start_ms": start_ms, "end_ms": end_ms, "text": subtitle_text})
    return entries


def _parse_srt_time(value: str) -> int | None:
    parts = value.replace(".", ",").split(",")
    if len(parts) != 2:
        return None
    hms = parts[0].split(":")
    if len(hms) != 3:
        return None
    try:
        hours, minutes, seconds = [int(part) for part in hms]
        millis = int(parts[1].ljust(3, "0")[:3])
    except ValueError:
        return None
    return hours * 3_600_000 + minutes * 60_000 + seconds * 1000 + millis


def _format_srt_time(value_ms: int) -> str:
    total_ms = max(0, int(value_ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
