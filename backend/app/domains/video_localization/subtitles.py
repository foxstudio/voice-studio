from __future__ import annotations

from pydantic import ValidationError

from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    VideoLocalizationSubtitleCueUpdate,
)

MIN_SUBTITLE_DURATION_MS = (1000 + 29) // 30
MAX_NEAREST_CUE_MATCH_GAP_MS = 1000


def export_srt(draft: VideoLocalizationDraft, kind: str) -> str:
    if kind not in {"en", "zh", "bilingual"}:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_KIND_UNSUPPORTED", "Subtitle kind must be en, zh, or bilingual")

    blocks: list[str] = []
    if kind == "zh":
        exportable = _exportable_localized_subtitles(draft)
        if not exportable:
            raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLES_EMPTY", "没有可导出的带时间码字幕。")
        for subtitle in exportable:
            if subtitle.end_ms <= subtitle.start_ms:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                    "字幕包含无效时间码，无法导出。",
                    {"cue_id": subtitle.linked_cue_id, "subtitle_id": subtitle.subtitle_id},
                )
            if not subtitle.text.strip():
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                    "字幕包含空文本，无法导出。",
                    {"cue_id": subtitle.linked_cue_id, "subtitle_id": subtitle.subtitle_id, "kind": kind},
                )
            blocks.append(
                "\n".join(
                    [
                        str(len(blocks) + 1),
                        f"{_format_srt_time(subtitle.start_ms)} --> {_format_srt_time(subtitle.end_ms)}",
                        subtitle.text,
                    ]
                )
            )
    else:
        if not draft.cues:
            raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLES_EMPTY", "没有可导出的带时间码字幕。")
        for cue in draft.cues:
            if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                    "字幕包含无效时间码，无法导出。",
                    {"cue_id": cue.cue_id},
                )
            lines = _subtitle_lines_for(cue, kind)
            if not lines:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                    "字幕包含空文本，无法导出。",
                    {"cue_id": cue.cue_id, "kind": kind},
                )
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
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLES_EMPTY", "没有可导出的带时间码字幕。")
    return "\n\n".join(blocks) + "\n"


def import_srt(
    draft: VideoLocalizationDraft,
    kind: str,
    srt_text: str,
    *,
    update_timing: bool = True,
    overwrite_tts: bool = False,
) -> VideoLocalizationDraft:
    if kind not in {"en", "zh", "tts"}:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_KIND_UNSUPPORTED", "Subtitle import kind must be en, zh, or tts")

    entries = _parse_srt(srt_text)
    if not entries:
        raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_EMPTY", "No valid SRT subtitle entries were found")
    _assert_entries_do_not_overlap(entries)

    if kind == "zh":
        return _import_localized_subtitles(draft, entries, overwrite_tts=overwrite_tts)

    if not draft.cues:
        if kind == "en":
            raise AppException(400, "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_NO_CUES", "Run source-language ASR before importing source subtitles")
        return draft.model_copy(
            update={
                "cues": [
                    _cue_from_tts_srt_entry(entry, index=index)
                    for index, entry in enumerate(entries, start=1)
                ]
            }
        )

    next_cues: list[VideoLocalizationCue] = []
    for index, cue in enumerate(draft.cues):
        entry = entries[index] if index < len(entries) else None
        if not entry:
            next_cues.append(cue)
            continue
        update: dict[str, object] = {}
        if update_timing:
            update["start_ms"] = entry["start_ms"]
            update["end_ms"] = entry["end_ms"]
            update["source_duration_ms"] = entry["end_ms"] - entry["start_ms"]
        if kind == "en":
            update["en_subtitle_text"] = entry["text"]
        else:
            update["tts_recommended_text"] = entry["text"]
        update["quality_flags"] = _merged_flags(cue.quality_flags, [f"{kind}_srt_import"])
        next_cues.append(VideoLocalizationCue(**{**cue.model_dump(), **update}))

    return draft.model_copy(update={"cues": next_cues})


def with_updated_localized_subtitle(
    draft: VideoLocalizationDraft,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
) -> VideoLocalizationDraft:
    update = patch.model_dump(exclude_unset=True)
    updated = False
    next_subtitles: list[VideoLocalizationSubtitleCue] = []
    for subtitle in draft.localized_subtitles:
        if subtitle.subtitle_id != subtitle_id:
            next_subtitles.append(subtitle)
            continue
        try:
            next_subtitles.append(VideoLocalizationSubtitleCue(**{**subtitle.model_dump(), **update}))
        except ValidationError as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_INVALID",
                "本土化字幕时间更新无效。",
                {"errors": exc.errors()},
            ) from exc
        updated = True
    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_NOT_FOUND", "本土化字幕不存在。")

    _assert_localized_subtitle_track_valid(next_subtitles, focus_subtitle_id=subtitle_id)
    ordered = _sort_localized_subtitles(next_subtitles)
    return draft.model_copy(
        update={
            "localized_subtitles": ordered,
            "cues": _sync_changed_localized_subtitle_to_cues(draft.cues, ordered, subtitle_id),
        }
    )


def without_localized_subtitle_track(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    next_cues = [
        cue.model_copy(
            update={
                "zh_localized_subtitle_text": None,
                "tts_recommended_text": None,
                "quality_flags": _without_localization_flags(cue.quality_flags),
            }
        )
        for cue in draft.cues
    ]
    return draft.model_copy(update={"localized_subtitles": [], "cues": next_cues, "localization_state": {}})


def _import_localized_subtitles(
    draft: VideoLocalizationDraft,
    entries: list[dict[str, int | str]],
    *,
    overwrite_tts: bool,
) -> VideoLocalizationDraft:
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=f"subtitle_{index:04d}",
            start_ms=int(entry["start_ms"]),
            end_ms=int(entry["end_ms"]),
            text=str(entry["text"]),
            tts_text=str(entry["text"]) if overwrite_tts else None,
            quality_flags=["zh_srt_import"],
        )
        for index, entry in enumerate(entries, start=1)
    ]
    _assert_localized_subtitle_track_valid(subtitles)
    base_cues = [
        cue.model_copy(
            update={
                "zh_localized_subtitle_text": None,
                **(
                    {
                        "tts_recommended_text": None,
                        "tts_result_id": None,
                        "tts_audio_path": None,
                        "tts_batch_task_id": None,
                        "tts_batch_status": None,
                        "tts_batch_error": None,
                        "tts_attempted_at": None,
                        "generated_duration_ms": None,
                    }
                    if overwrite_tts
                    else {}
                ),
                "quality_flags": _without_localization_flags(cue.quality_flags),
            }
        )
        for cue in draft.cues
    ]
    next_subtitles, next_cues = _mirror_localized_subtitles_to_cues(
        base_cues,
        subtitles,
        sync_tts=overwrite_tts,
        preserve_tts=not overwrite_tts,
    )
    return draft.model_copy(update={"localized_subtitles": next_subtitles, "cues": next_cues, "localization_state": {}})


def _sync_changed_localized_subtitle_to_cues(
    cues: list[VideoLocalizationCue],
    subtitles: list[VideoLocalizationSubtitleCue],
    changed_subtitle_id: str,
) -> list[VideoLocalizationCue]:
    changed = next((item for item in subtitles if item.subtitle_id == changed_subtitle_id), None)
    if changed is None:
        return cues
    changed_cue_ids = _localized_subtitle_source_cue_ids(changed)
    if not changed_cue_ids:
        return cues

    next_cues: list[VideoLocalizationCue] = []
    for cue in cues:
        if cue.cue_id not in changed_cue_ids:
            next_cues.append(cue)
            continue
        related = [
            item
            for item in subtitles
            if cue.cue_id in _localized_subtitle_source_cue_ids(item)
        ]
        display_text = "\n".join(item.text.strip() for item in related if item.text.strip()) or None
        tts_parts = [item.tts_text.strip() for item in related if item.tts_text and item.tts_text.strip()]
        tts_text = "\n".join(tts_parts) if tts_parts else None
        update: dict[str, object] = {
            "zh_localized_subtitle_text": display_text,
            "quality_flags": _merged_flags(cue.quality_flags, ["localized_track_sync"]),
        }
        if any(item.tts_text is not None for item in related):
            update["tts_recommended_text"] = tts_text
        next_cues.append(cue.model_copy(update=update))
    return next_cues


def _localized_subtitle_source_cue_ids(subtitle: VideoLocalizationSubtitleCue) -> list[str]:
    return list(dict.fromkeys(subtitle.source_cue_ids or ([subtitle.linked_cue_id] if subtitle.linked_cue_id else [])))


def _without_localization_flags(flags: list[str]) -> list[str]:
    return [
        flag
        for flag in flags
        if flag not in {
            "zh_srt_import",
            "localized_track_sync",
            "tts_batch_submitted",
            "tts_failed",
            "tts_generated",
        }
        and not flag.startswith("localization")
    ]


def _mirror_localized_subtitles_to_cues(
    cues: list[VideoLocalizationCue],
    subtitles: list[VideoLocalizationSubtitleCue],
    *,
    sync_tts: bool = False,
    preserve_tts: bool = False,
) -> tuple[list[VideoLocalizationSubtitleCue], list[VideoLocalizationCue]]:
    if not cues or not subtitles:
        return subtitles, cues

    timed_cues = [
        (index, cue)
        for index, cue in enumerate(cues)
        if cue.start_ms is not None and cue.end_ms is not None and cue.end_ms > cue.start_ms
    ]
    if not timed_cues:
        return subtitles, cues

    assignments = _match_localized_subtitles_to_cues(subtitles, timed_cues)
    subtitles_by_cue_index: dict[int, list[VideoLocalizationSubtitleCue]] = {}
    next_subtitles: list[VideoLocalizationSubtitleCue] = []

    for subtitle_index, subtitle in enumerate(subtitles):
        matched_cues = assignments.get(subtitle_index, [])
        if not matched_cues:
            next_subtitles.append(subtitle.model_copy(update={"linked_cue_id": None, "source_cue_ids": []}))
            continue

        primary_cue = matched_cues[0][1]
        preserved_tts = (
            primary_cue.tts_recommended_text.strip()
            if preserve_tts and primary_cue.tts_recommended_text
            else None
        )
        linked_subtitle = subtitle.model_copy(
            update={
                "linked_cue_id": primary_cue.cue_id,
                "source_cue_ids": [cue.cue_id for _cue_index, cue in matched_cues],
                "tts_text": subtitle.tts_text or preserved_tts,
                "quality_flags": _merged_flags(subtitle.quality_flags, ["linked_by_timing"]),
            }
        )
        next_subtitles.append(linked_subtitle)
        for cue_index, _cue in matched_cues:
            subtitles_by_cue_index.setdefault(cue_index, []).append(linked_subtitle)

    next_cues: list[VideoLocalizationCue] = []
    for cue_index, cue in enumerate(cues):
        related = subtitles_by_cue_index.get(cue_index)
        if not related:
            next_cues.append(cue)
            continue
        display_text = "\n".join(item.text.strip() for item in related if item.text.strip()) or None
        cue_update: dict[str, object] = {
            "zh_localized_subtitle_text": display_text,
            "quality_flags": _merged_flags(cue.quality_flags, ["zh_srt_import"]),
        }
        if sync_tts:
            tts_parts = [item.tts_text.strip() for item in related if item.tts_text and item.tts_text.strip()]
            cue_update["tts_recommended_text"] = "\n".join(tts_parts) if tts_parts else None
        next_cues.append(cue.model_copy(update=cue_update))
    return next_subtitles, next_cues


def _match_localized_subtitles_to_cues(
    subtitles: list[VideoLocalizationSubtitleCue],
    timed_cues: list[tuple[int, VideoLocalizationCue]],
) -> dict[int, list[tuple[int, VideoLocalizationCue]]]:
    assignments: dict[int, list[tuple[int, VideoLocalizationCue]]] = {}
    ordered_cues = sorted(
        timed_cues,
        key=lambda item: (item[1].start_ms or 0, item[1].end_ms or 0, item[0]),
    )

    ordered_subtitles = sorted(
        enumerate(subtitles),
        key=lambda item: (item[1].start_ms, item[1].end_ms, item[1].subtitle_id),
    )
    for original_index, subtitle in ordered_subtitles:
        overlapping = [
            (cue_index, cue)
            for cue_index, cue in ordered_cues
            if min(subtitle.end_ms, cue.end_ms or 0) > max(subtitle.start_ms, cue.start_ms or 0)
        ]
        if overlapping:
            assignments[original_index] = overlapping
            continue

        cue_index, matched_cue = max(
            ordered_cues,
            key=lambda item: _matching_score(
                subtitle.start_ms,
                subtitle.end_ms,
                item[1].start_ms or 0,
                item[1].end_ms or 0,
            ),
        )
        gap_ms = _timing_gap(
            subtitle.start_ms,
            subtitle.end_ms,
            matched_cue.start_ms or 0,
            matched_cue.end_ms or 0,
        )
        if gap_ms <= MAX_NEAREST_CUE_MATCH_GAP_MS:
            assignments[original_index] = [(cue_index, matched_cue)]
    return assignments


def _matching_score(subtitle_start_ms: int, subtitle_end_ms: int, cue_start_ms: int, cue_end_ms: int) -> tuple[int, int, int, int]:
    overlap_ms = max(0, min(subtitle_end_ms, cue_end_ms) - max(subtitle_start_ms, cue_start_ms))
    gap_ms = _timing_gap(subtitle_start_ms, subtitle_end_ms, cue_start_ms, cue_end_ms)
    subtitle_midpoint = subtitle_start_ms + (subtitle_end_ms - subtitle_start_ms) // 2
    cue_midpoint = cue_start_ms + (cue_end_ms - cue_start_ms) // 2
    return (
        1 if overlap_ms > 0 else 0,
        overlap_ms,
        -gap_ms,
        -abs(subtitle_midpoint - cue_midpoint),
    )


def _timing_gap(start_a_ms: int, end_a_ms: int, start_b_ms: int, end_b_ms: int) -> int:
    overlap_ms = max(0, min(end_a_ms, end_b_ms) - max(start_a_ms, start_b_ms))
    if overlap_ms > 0:
        return 0
    if end_a_ms <= start_b_ms:
        return start_b_ms - end_a_ms
    return start_a_ms - end_b_ms


def _cue_from_tts_srt_entry(entry: dict[str, int | str], *, index: int) -> VideoLocalizationCue:
    text = str(entry["text"])
    start_ms = int(entry["start_ms"])
    end_ms = int(entry["end_ms"])
    values = {
        "cue_id": f"cue_{index:04d}",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_duration_ms": end_ms - start_ms,
        "quality_flags": ["tts_srt_import"],
        "tts_recommended_text": text,
    }
    return VideoLocalizationCue(**values)


def _exportable_localized_subtitles(draft: VideoLocalizationDraft) -> list[VideoLocalizationSubtitleCue]:
    if draft.localized_subtitles:
        return _sort_localized_subtitles(draft.localized_subtitles)

    fallback: list[VideoLocalizationSubtitleCue] = []
    for index, cue in enumerate(draft.cues, start=1):
        if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                "字幕包含无效时间码，无法导出。",
                {"cue_id": cue.cue_id},
            )
        text = (cue.zh_localized_subtitle_text or "").strip()
        if not text:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                "字幕包含空文本，无法导出。",
                {"cue_id": cue.cue_id, "kind": "zh"},
            )
        fallback.append(
            VideoLocalizationSubtitleCue(
                subtitle_id=f"cue_{index:04d}",
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=text,
                linked_cue_id=cue.cue_id,
            )
        )
    return fallback


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


def _assert_entries_do_not_overlap(entries: list[dict[str, int | str]]) -> None:
    ordered = sorted(entries, key=lambda entry: (int(entry["start_ms"]), int(entry["end_ms"])))
    for previous, current in zip(ordered, ordered[1:]):
        previous_end_ms = int(previous["end_ms"])
        current_start_ms = int(current["start_ms"])
        if current_start_ms >= previous_end_ms:
            continue
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SUBTITLE_TRACK_OVERLAP",
            "导入失败：同一字幕轨内存在时间重叠，请先修正 SRT。",
            {
                "previous_start_ms": int(previous["start_ms"]),
                "previous_end_ms": previous_end_ms,
                "current_start_ms": current_start_ms,
                "current_end_ms": int(current["end_ms"]),
            },
        )


def _assert_localized_subtitle_track_valid(
    subtitles: list[VideoLocalizationSubtitleCue],
    *,
    focus_subtitle_id: str | None = None,
) -> None:
    ordered = _sort_localized_subtitles(subtitles)
    for subtitle in ordered:
        duration_ms = subtitle.end_ms - subtitle.start_ms
        if duration_ms < MIN_SUBTITLE_DURATION_MS:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_TOO_SHORT",
                "本土化字幕时长至少需要 1 帧（30fps）。",
                {
                    "subtitle_id": subtitle.subtitle_id,
                    "start_ms": subtitle.start_ms,
                    "end_ms": subtitle.end_ms,
                    "minimum_duration_ms": MIN_SUBTITLE_DURATION_MS,
                },
            )
    for previous, current in zip(ordered, ordered[1:]):
        if current.start_ms >= previous.end_ms:
            continue
        if focus_subtitle_id and focus_subtitle_id not in {previous.subtitle_id, current.subtitle_id}:
            continue
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_OVERLAP",
            "本土化字幕时间不能重叠，请将当前字幕限制在相邻字幕的出入点之间。",
            {
                "subtitle_id": focus_subtitle_id or current.subtitle_id,
                "previous_subtitle_id": previous.subtitle_id,
                "current_subtitle_id": current.subtitle_id,
                "overlap_start_ms": current.start_ms,
                "overlap_end_ms": previous.end_ms,
            },
        )


def _sort_localized_subtitles(subtitles: list[VideoLocalizationSubtitleCue]) -> list[VideoLocalizationSubtitleCue]:
    return sorted(subtitles, key=lambda subtitle: (subtitle.start_ms, subtitle.end_ms, subtitle.subtitle_id))


def _merged_flags(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    for flag in additions:
        if flag not in merged:
            merged.append(flag)
    return merged


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
