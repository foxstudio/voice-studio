from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.domains.video_localization import media_assets, media_health
from app.domains.video_localization.tts_selection import TtsSelectionSnapshot
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.voice_studio import GenerateRequest, LicenseStatus, VideoLocalizationCue
from app.services import reference_audio_integrity, settings_store, text_normalizer, voice_store


REFERENCE_EXPANSION_THRESHOLD_MS = 2_000
REFERENCE_EXPANSION_TARGET_MS = 2_500
SAME_SPEAKER_REFERENCE_MIN_MS = 3_000
SAME_SPEAKER_REFERENCE_MAX_MS = 6_500


def _tts_language_for_project(draft: VideoLocalizationDraft) -> str:
    """Resolve the project target language to the shared TTS/ASR contract."""
    target = str(draft.language_config.target_language or "").strip().lower().replace("_", "-")
    aliases = {
        "chinese": "zh",
        "中文": "zh",
        "english": "en",
        "英文": "en",
    }
    target = aliases.get(target, target)
    primary = target.split("-", 1)[0]
    return primary if primary in {"zh", "en"} else "auto"


def build_selection_handoff(
    project_id: str,
    draft: VideoLocalizationDraft,
    selection: TtsSelectionSnapshot,
    *,
    reference_start_ms: int | None = None,
    reference_end_ms: int | None = None,
) -> GenerateRequest:
    """Build TTS parameters from independent target and source selections."""
    vocals_path = media_health.inspect_project_media(draft).paths.vocals
    if vocals_path is None:
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "请先准备人声轨，再生成字幕配音")
    source = selection.source
    target = selection.target
    if source.end_ms <= source.start_ms:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_SOURCE_RANGE_INVALID", "所选原文字幕的时间范围无效")

    source_identity = _source_identity(vocals_path)
    source_file = voice_store.ensure_managed_audio_file(
        vocals_path,
        file_id=_managed_id("vl_source", project_id, source_identity),
        original_name=f"{project_id}-vocals.wav",
    )
    requested_reference_start_ms = reference_start_ms
    requested_reference_end_ms = reference_end_ms
    reference_start_ms = max(
        0,
        min(
            source.start_ms if reference_start_ms is None else reference_start_ms,
            source_file.duration_ms - 1,
        ),
    )
    reference_end_ms = max(
        reference_start_ms + 1,
        min(
            source.end_ms if reference_end_ms is None else reference_end_ms,
            source_file.duration_ms,
        ),
    )
    clip = voice_store.create_audio_clip(
        source_file.file_id,
        reference_start_ms,
        reference_end_ms,
        clip_file_id=_managed_id(
            "vl_clip_v2",
            project_id,
            target.segment_id,
            *source.cue_ids,
            str(reference_start_ms),
            str(reference_end_ms),
            source_identity,
        ),
    )
    clip_voice_file = clip.get("voice_file")
    clip_duration_ms = max(
        1,
        int(getattr(clip_voice_file, "duration_ms", 0) or reference_end_ms - reference_start_ms),
    )
    materialized_reference_end_ms = min(source_file.duration_ms, reference_start_ms + clip_duration_ms)
    reference_text = source.ref_text
    reference_tags = ["视频本土化", "双轨独立选择", source.speaker_id]
    if (
        requested_reference_start_ms is not None
        and requested_reference_end_ms is not None
        and (
            reference_start_ms != source.start_ms
            or materialized_reference_end_ms != source.end_ms
        )
    ):
        reference_cues = [
            cue
            for cue in draft.cues
            if cue.start_ms is not None
            and cue.end_ms is not None
            and cue.start_ms < materialized_reference_end_ms
            and _cue_acoustic_end_ms(cue) > reference_start_ms
            and (
                source.speaker_id == "unknown"
                or not cue.speaker_id
                or cue.speaker_id == source.speaker_id
            )
        ]
        reference_text = _source_text_for_range(
            draft,
            reference_cues,
            reference_start_ms,
            materialized_reference_end_ms,
        ) or source.ref_text
        reference_tags.append("同说话人长参考")
    settings = settings_store.get()
    return GenerateRequest(
        text=target.text,
        engine_id=settings.default_engine_id,
        source="video_localization",
        project_id=project_id,
        segment_id=target.segment_id,
        localized_subtitle_id=target.subtitle_ids[0],
        cue_id=source.cue_ids[0],
        bind_to_video_localization=True,
        video_localization_start_ms=target.start_ms,
        video_localization_end_ms=target.end_ms,
        video_localization_target_subtitle_ids=target.subtitle_ids,
        video_localization_source_cue_ids=source.cue_ids,
        voice_source="reference_audio",
        reference_audio_path=clip["path"],
        reference_audio_license_status=LicenseStatus.localized,
        reference_audio_tags=reference_tags,
        ref_text=reference_text or None,
        custom_reference_source_audio_path=source_file.path,
        custom_reference_source_duration_ms=source_file.duration_ms,
        custom_reference_trim_start_ms=reference_start_ms,
        custom_reference_trim_end_ms=materialized_reference_end_ms,
        language=_tts_language_for_project(draft),
        emotion_mode="follow_reference",
        output_format=settings.default_output_format,
    )


def best_same_speaker_reference_range(
    draft: VideoLocalizationDraft,
    *,
    speaker_id: str | None,
    current_start_ms: int,
    current_end_ms: int,
) -> tuple[int, int]:
    """Reuse a clearly usable longer same-speaker prompt for short groups.

    Duration alone is not enough: long diarization spans can be mostly silence
    or belong to a neighbouring speaker.  A reusable prompt therefore needs a
    bounded duration and a minimally plausible transcript density.  If that
    evidence is absent, callers keep the current range and can explicitly use
    the user-approved voice-library fallback instead.
    """

    current_start_ms = int(current_start_ms)
    current_end_ms = int(current_end_ms)
    if (
        not speaker_id
        or speaker_id == "unknown"
        or current_end_ms - current_start_ms >= SAME_SPEAKER_REFERENCE_MIN_MS
    ):
        return current_start_ms, current_end_ms

    candidates: list[tuple[int, int, int, int, int, int]] = []
    for clip in draft.reference_clips:
        if clip.speaker_id != speaker_id or clip.source_stem != "vocals_clean":
            continue
        start_ms = int(clip.start_ms)
        end_ms = int(clip.end_ms)
        duration_ms = max(0, int(clip.duration_ms or end_ms - start_ms))
        if not (
            SAME_SPEAKER_REFERENCE_MIN_MS
            <= duration_ms
            <= SAME_SPEAKER_REFERENCE_MAX_MS
        ):
            continue
        transcript = str(clip.asr_text or "").strip()
        token_count = len(
            re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]", transcript)
        )
        duration_seconds = max(0.001, duration_ms / 1_000)
        token_rate = token_count / duration_seconds
        if token_count < 3 or not 1.0 <= token_rate <= 5.0:
            continue
        clean_rank = 0 if clip.cleanliness in {"clean", "verified", "passed"} else 1
        if end_ms <= current_start_ms:
            temporal_distance_ms = current_start_ms - end_ms
        elif start_ms >= current_end_ms:
            temporal_distance_ms = start_ms - current_end_ms
        else:
            temporal_distance_ms = 0
        candidates.append(
            (
                clean_rank,
                temporal_distance_ms,
                abs(duration_ms - 4_500),
                -token_count,
                start_ms,
                end_ms,
            )
        )
    if not candidates:
        return current_start_ms, current_end_ms
    (
        _clean_rank,
        _temporal_distance_ms,
        _duration_distance,
        _negative_tokens,
        start_ms,
        end_ms,
    ) = min(candidates)
    return start_ms, end_ms


def _expanded_reference_range(
    start_ms: int,
    end_ms: int,
    source_duration_ms: int,
    min_start_ms: int = 0,
    max_end_ms: int | None = None,
) -> tuple[int, int]:
    source_duration_ms = max(1, int(source_duration_ms))
    min_start_ms = max(0, min(int(min_start_ms), source_duration_ms - 1))
    max_end_ms = max(min_start_ms + 1, min(int(max_end_ms or source_duration_ms), source_duration_ms))
    start_ms = max(min_start_ms, min(int(start_ms), max_end_ms - 1))
    end_ms = max(start_ms + 1, min(int(end_ms), max_end_ms))
    # Preserve a reviewed speech boundary once it is long enough to provide a
    # useful prompt. Expanding a complete clause by a few hundred
    # milliseconds can cut neighbouring speech mid-phrase and make OmniVoice
    # leak that unfinished English tail into the generated Chinese audio.
    # This is a reference-selection preference, not a generation constraint:
    # callers may still submit shorter materialized clips unchanged.
    if end_ms - start_ms >= REFERENCE_EXPANSION_THRESHOLD_MS:
        return start_ms, end_ms
    target_duration_ms = min(max_end_ms - min_start_ms, REFERENCE_EXPANSION_TARGET_MS)
    missing_ms = max(0, target_duration_ms - (end_ms - start_ms))
    if not missing_ms:
        return start_ms, end_ms
    expanded_start_ms = max(min_start_ms, start_ms - missing_ms // 2)
    expanded_end_ms = min(max_end_ms, end_ms + (missing_ms - (start_ms - expanded_start_ms)))
    if expanded_end_ms - expanded_start_ms < target_duration_ms:
        expanded_start_ms = max(min_start_ms, expanded_end_ms - target_duration_ms)
    return expanded_start_ms, expanded_end_ms


def _speaker_reference_bounds(
    draft: VideoLocalizationDraft,
    speaker_id: str,
    start_ms: int,
    end_ms: int,
    source_duration_ms: int,
) -> tuple[int, int]:
    if speaker_id == "unknown":
        return 0, source_duration_ms
    min_start_ms = 0
    max_end_ms = source_duration_ms
    for cue in draft.cues:
        if not cue.speaker_id or cue.speaker_id == speaker_id or cue.start_ms is None or cue.end_ms is None:
            continue
        cue_end_ms = _cue_acoustic_end_ms(cue)
        if cue_end_ms <= start_ms:
            min_start_ms = max(min_start_ms, cue_end_ms)
        elif cue.start_ms >= end_ms:
            max_end_ms = min(max_end_ms, cue.start_ms)
    return min_start_ms, max(min_start_ms + 1, max_end_ms)


def _cue_acoustic_end_ms(cue: VideoLocalizationCue) -> int:
    end_ms = int(cue.end_ms or 0)
    if cue.start_ms is None or cue.source_duration_ms is None:
        return end_ms
    return min(
        end_ms,
        int(cue.start_ms) + int(cue.source_duration_ms),
    )


def _managed_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _source_identity(source_path: str) -> str:
    path = Path(source_path)
    try:
        stat = path.stat()
    except OSError:
        return str(path)
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _source_text_for_range(
    draft: VideoLocalizationDraft,
    source_cues: list[VideoLocalizationCue],
    start_ms: int,
    end_ms: int,
) -> str:
    source_word_ids = {word_id for item in source_cues for word_id in item.source_word_ids}
    words = [] if draft.transcription is None else sorted(
        (
            word
            for word in draft.transcription.words
            if (word.end_ms > start_ms and word.start_ms < end_ms)
            and (not source_word_ids or word.word_id in source_word_ids)
        ),
        key=lambda word: (word.start_ms, word.end_ms),
    )
    if words:
        parts: list[str] = []
        previous_key = ""
        for word in words:
            text = word.text.strip()
            key = re.sub(r"[^\w]+", "", text, flags=re.UNICODE).casefold()
            if key and key == previous_key:
                continue
            parts.append(text)
            previous_key = key
        return " ".join(parts).strip()
    # A reviewed ASR sentence can be split across several timeline cues. Each
    # cue keeps the full raw sentence for audit, so concatenating source_text_raw
    # repeats the same sentence and gives voice-cloning engines a false prompt.
    # The display subtitle is the cue-local partition and is the safe fallback
    # when word-level alignment is unavailable.
    cue_text = " ".join((item.en_subtitle_text or "").strip() for item in source_cues).strip()
    if cue_text:
        return cue_text
    raw_parts = list(
        dict.fromkeys((item.source_text_raw or "").strip() for item in source_cues if (item.source_text_raw or "").strip())
    )
    return " ".join(raw_parts).strip()
