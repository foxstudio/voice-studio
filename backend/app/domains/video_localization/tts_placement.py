from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationSubtitleCue


@dataclass(frozen=True)
class FrozenTtsPlacementTarget:
    segment_id: str
    target_subtitle_ids: tuple[str, ...]
    source_cue_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    text: str | None = None


@dataclass(frozen=True)
class TtsResultMetadata:
    result_id: str
    output_path: str
    duration_ms: int | None = None
    task_id: str | None = None
    generation_id: str | None = None
    timeline_clip_id: str | None = None
    candidate_id: str | None = None
    placement_start_ms: int | None = None
    placement_end_ms: int | None = None
    source_start_ms: int = 0
    source_end_ms: int | None = None
    speech_onset_ms: int | None = None
    alignment_lead_ms: int = 0


@dataclass(frozen=True)
class TtsPlacementResult:
    draft: VideoLocalizationDraft
    clip_id: str
    mirrored_subtitle_ids: tuple[str, ...]


def replace_with_local_phrase(
    draft: VideoLocalizationDraft,
    *,
    candidate_clip_id: str,
    replace_clip_ids: Sequence[str],
    target_start_ms: int,
    target_end_ms: int,
    source_start_ms: int,
    source_end_ms: int,
    target_text: str,
) -> VideoLocalizationDraft:
    """Atomically adopt a ready local phrase and remove superseded dub clips.

    Generation remains on the canonical TTS path.  This command only commits
    an already playable candidate, so a failed or unfinished generation can
    never erase the currently accepted timeline audio.
    """

    candidate_id = str(candidate_clip_id or "").strip()
    replaced_ids = tuple(
        dict.fromkeys(str(value).strip() for value in replace_clip_ids if str(value).strip())
    )
    if not candidate_id or not replaced_ids:
        raise ValueError("local phrase repair requires candidate and replaced clip IDs")
    if candidate_id in replaced_ids:
        raise ValueError("candidate clip cannot replace itself")
    if target_start_ms < 0 or target_end_ms <= target_start_ms:
        raise ValueError("local phrase target range is invalid")
    if source_start_ms < 0 or source_end_ms <= source_start_ms:
        raise ValueError("local phrase source range is invalid")
    if not target_text.strip():
        raise ValueError("local phrase target text is required")

    clips_by_id = {
        str(item.get("clip_id") or ""): dict(item)
        for item in draft.timeline_clips
        if str(item.get("clip_id") or "")
    }
    candidate = clips_by_id.get(candidate_id)
    if candidate is None:
        raise KeyError(f"candidate clip not found: {candidate_id}")
    missing = [clip_id for clip_id in replaced_ids if clip_id not in clips_by_id]
    if missing:
        raise KeyError(f"replaced clip not found: {', '.join(missing)}")
    if candidate.get("track_id", "dub") != "dub":
        raise ValueError("local phrase candidate must be a dub clip")
    if candidate.get("status") != "ready" or not candidate.get("audio_path"):
        raise ValueError("local phrase candidate must be ready and playable")
    replaced = [clips_by_id[clip_id] for clip_id in replaced_ids]
    if any(item.get("track_id", "dub") != "dub" for item in replaced):
        raise ValueError("local phrase replacement is limited to dub clips")

    available_source_end = candidate.get("source_end_ms")
    if available_source_end is not None and source_end_ms > int(available_source_end):
        raise ValueError("local phrase source range exceeds candidate audio")
    lane = min(max(0, int(item.get("dub_lane") or 0)) for item in replaced)
    committed = {
        **candidate,
        "start_ms": target_start_ms,
        "end_ms": target_end_ms,
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "dub_lane": lane,
        "tts_target_text": target_text.strip(),
        "local_phrase_repair": {
            "schema_version": "video-localization-local-phrase-repair-v1",
            "replaced_clip_ids": list(replaced_ids),
        },
    }
    removed = set(replaced_ids)
    next_clips = [
        committed if str(item.get("clip_id") or "") == candidate_id else dict(item)
        for item in draft.timeline_clips
        if str(item.get("clip_id") or "") not in removed
    ]
    return draft.model_copy(update={"timeline_clips": next_clips})


def with_explicit_single_tts_result(
    draft: VideoLocalizationDraft,
    *,
    segment_id: str,
    target_subtitle_ids: Sequence[str],
    source_cue_ids: Sequence[str],
    target_start_ms: int,
    target_end_ms: int,
    target_text: str | None = None,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
    generation_id: str | None = None,
    timeline_clip_id: str | None = None,
    candidate_id: str | None = None,
) -> VideoLocalizationDraft:
    """Production-facing adapter for explicit v2 selection placement."""

    return place_frozen_tts_result(
        draft,
        FrozenTtsPlacementTarget(
            segment_id=segment_id,
            target_subtitle_ids=tuple(target_subtitle_ids),
            source_cue_ids=tuple(source_cue_ids),
            start_ms=target_start_ms,
            end_ms=target_end_ms,
            text=target_text,
        ),
        TtsResultMetadata(
            result_id=result_id,
            output_path=output_path,
            duration_ms=duration_ms,
            task_id=task_id,
            generation_id=generation_id,
            timeline_clip_id=timeline_clip_id,
            candidate_id=candidate_id,
        ),
    ).draft


def place_frozen_tts_result(
    draft: VideoLocalizationDraft,
    target: FrozenTtsPlacementTarget,
    result: TtsResultMetadata,
) -> TtsPlacementResult:
    """Place a generated take without removing other takes for its subtitles.

    The segment ID remains an opaque display/workflow identity. It is never
    parsed to rediscover a current subtitle range. A multi-subtitle result is
    represented by its dub clip only; mirroring the whole audio onto one child
    subtitle would falsely claim that child owns the combined result.

    A new generation appends a clip. Callback replay resolves the same result
    identity; an explicit timeline_clip_id replaces only that clip. Sharing
    subtitle targets is never authorization to remove another timeline take.
    """

    target_ids = _unique_nonempty(target.target_subtitle_ids)
    source_ids = _unique_nonempty(target.source_cue_ids)
    if not target.segment_id:
        raise ValueError("TTS placement requires segment_id")
    if not target_ids:
        raise ValueError("TTS placement requires target subtitle IDs")
    if target.start_ms < 0 or target.end_ms <= target.start_ms:
        raise ValueError("TTS placement target range is invalid")
    if not result.result_id or not result.output_path:
        raise ValueError("TTS placement requires result ID and output path")

    subtitle_by_id = {item.subtitle_id: item for item in draft.localized_subtitles}
    missing_ids = [subtitle_id for subtitle_id in target_ids if subtitle_id not in subtitle_by_id]
    if missing_ids:
        raise KeyError(f"target subtitle not found: {', '.join(missing_ids)}")

    clip_index, clip_id = _resolve_target_clip(draft.timeline_clips, target, result)
    timing = _clip_timing(target, result)
    base_clip = dict(draft.timeline_clips[clip_index]) if clip_index is not None else {}
    base_clip.pop("speech_onset_ms", None)
    for legacy_field in (
        "cqc_status",
        "cqc_report",
        "cqc_report_version",
        "timeline_edit_gate",
    ):
        base_clip.pop(legacy_field, None)
    if result.candidate_id is None:
        base_clip.pop("candidate_id", None)
    clip = {
        **base_clip,
        "clip_id": clip_id,
        "track_id": "dub",
        "subtitle_id": target.segment_id,
        "target_subtitle_ids": list(target_ids),
        "target_start_ms": target.start_ms,
        "target_end_ms": target.end_ms,
        "tts_target_binding_status": "current",
        "cue_id": source_ids[0] if source_ids else None,
        "source_cue_ids": list(source_ids),
        "result_id": result.result_id,
        "task_id": result.task_id or base_clip.get("task_id"),
        "generation_id": result.generation_id or result.task_id or base_clip.get("generation_id"),
        "audio_path": result.output_path,
        "status": "ready",
        **({"candidate_id": result.candidate_id} if result.candidate_id is not None else {}),
        **timing,
    }
    if target.text is not None:
        clip["tts_target_text"] = target.text
    superseded = {clip_index} if clip_index is not None else set()
    next_clips: list[dict] = []
    inserted = False
    for index, item in enumerate(draft.timeline_clips):
        if index == clip_index:
            next_clips.append(clip)
            inserted = True
        elif index not in superseded:
            next_clips.append(dict(item))
    if not inserted:
        next_clips.append(clip)

    mirrored_ids: tuple[str, ...] = ()
    displaced_target_ids = {
        subtitle_id
        for index in superseded
        for subtitle_id in _clip_target_subtitle_ids(draft.timeline_clips[index])
    }
    next_subtitles = [
        _without_result_mirror(item)
        if item.subtitle_id in displaced_target_ids
        else item
        for item in draft.localized_subtitles
    ]
    if len(target_ids) == 1:
        subtitle_id = target_ids[0]
        next_subtitles = [
            _with_result_mirror(item, result)
            if item.subtitle_id == subtitle_id
            else item
            for item in draft.localized_subtitles
        ]
        mirrored_ids = (subtitle_id,)

    return TtsPlacementResult(
        draft=draft.model_copy(
            update={
                "localized_subtitles": next_subtitles,
                "timeline_clips": next_clips,
            }
        ),
        clip_id=clip_id,
        mirrored_subtitle_ids=mirrored_ids,
    )


def _resolve_target_clip(
    clips: Sequence[dict],
    target: FrozenTtsPlacementTarget,
    result: TtsResultMetadata,
) -> tuple[int | None, str]:
    explicit_clip_id = str(result.timeline_clip_id or "")
    if explicit_clip_id:
        matches = [index for index, item in enumerate(clips) if str(item.get("clip_id") or "") == explicit_clip_id]
        if len(matches) > 1:
            raise ValueError(f"duplicate timeline clip ID: {explicit_clip_id}")
        return (matches[0] if matches else None), explicit_clip_id

    result_identities = {
        str(value)
        for value in (result.task_id, result.generation_id, result.result_id)
        if value
    }
    frozen_ids = list(_unique_nonempty(target.target_subtitle_ids))
    if result_identities:
        matches = [
            index
            for index, item in enumerate(clips)
            if (
                str(item.get("subtitle_id") or "") == target.segment_id
                and [str(value) for value in item.get("target_subtitle_ids") or []] == frozen_ids
                and (
                    bool(
                        result_identities
                        & {
                            str(value)
                            for value in (
                                item.get("task_id"),
                                item.get("generation_id"),
                                item.get("result_id"),
                            )
                            if value
                        }
                    )
                    or (
                        result.task_id
                        and str(item.get("candidate_id") or "").endswith(result.task_id)
                    )
                )
            )
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous timeline clips for TTS result")
        if matches:
            index = matches[0]
            return index, str(clips[index].get("clip_id") or _default_clip_id(target))

    # Callback retries remain idempotent through the result identities above.
    # A new result receives its own timeline identity, even for the same target.
    return None, _next_clip_id(clips, target)


def _clip_target_subtitle_ids(clip: dict) -> tuple[str, ...]:
    explicit = _unique_nonempty(clip.get("target_subtitle_ids") or ())
    if explicit:
        return explicit
    subtitle_id = str(clip.get("subtitle_id") or "")
    return (subtitle_id,) if subtitle_id else ()


def _next_clip_id(
    clips: Sequence[dict],
    target: FrozenTtsPlacementTarget,
) -> str:
    used_ids = {str(item.get("clip_id") or "") for item in clips}
    base_id = _default_clip_id(target)
    clip_id = base_id
    suffix = 2
    while clip_id in used_ids:
        clip_id = f"{base_id}_{suffix}"
        suffix += 1
    return clip_id


def _default_clip_id(target: FrozenTtsPlacementTarget) -> str:
    target_ids = _unique_nonempty(target.target_subtitle_ids)
    if len(target_ids) == 1 and target.segment_id == target_ids[0]:
        return f"clip_{target.segment_id}"
    digest = hashlib.sha256("\0".join(target_ids).encode("utf-8")).hexdigest()[:12]
    return f"clip_{target.segment_id}_{digest}"


def _clip_timing(
    target: FrozenTtsPlacementTarget,
    result: TtsResultMetadata,
) -> dict[str, int]:
    source_start_ms = max(0, int(result.source_start_ms))
    effective_duration_ms = max(1, int(result.duration_ms or (target.end_ms - target.start_ms)))
    source_end_ms = max(source_start_ms + 1, int(result.source_end_ms or effective_duration_ms))
    start_ms = max(0, int(result.placement_start_ms if result.placement_start_ms is not None else target.start_ms))
    end_ms = int(
        result.placement_end_ms
        if result.placement_end_ms is not None
        else start_ms + (source_end_ms - source_start_ms)
    )
    if end_ms <= start_ms:
        raise ValueError("TTS result placement range is invalid")
    timing = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "alignment_lead_ms": max(0, int(result.alignment_lead_ms)),
    }
    if result.speech_onset_ms is not None:
        timing["speech_onset_ms"] = max(0, int(result.speech_onset_ms))
    return timing


def _with_result_mirror(
    subtitle: VideoLocalizationSubtitleCue,
    result: TtsResultMetadata,
) -> VideoLocalizationSubtitleCue:
    return subtitle.model_copy(
        update={
            "tts_result_id": result.result_id,
            "tts_generation_id": result.generation_id or result.task_id,
            "tts_audio_path": result.output_path,
            "generated_duration_ms": result.duration_ms,
            "quality_flags": list(dict.fromkeys([*subtitle.quality_flags, "tts_generated"])),
        }
    )


def _without_result_mirror(
    subtitle: VideoLocalizationSubtitleCue,
) -> VideoLocalizationSubtitleCue:
    return subtitle.model_copy(
        update={
            "tts_result_id": None,
            "tts_generation_id": None,
            "tts_audio_path": None,
            "generated_duration_ms": None,
            "quality_flags": [
                flag for flag in subtitle.quality_flags if flag != "tts_generated"
            ],
        }
    )


def _unique_nonempty(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))
