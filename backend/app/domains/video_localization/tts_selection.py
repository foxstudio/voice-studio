from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.errors import AppException
from app.services import text_normalizer


class TtsSelectionRequest(BaseModel):
    target_subtitle_ids: list[str] = Field(min_length=1)
    source_cue_ids: list[str] = Field(default_factory=list)


class TtsTargetSnapshot(BaseModel):
    subtitle_ids: list[str]
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    binding_fingerprint: str


class TtsSourceSnapshot(BaseModel):
    cue_ids: list[str]
    word_ids: list[str]
    start_ms: int
    end_ms: int
    speaker_id: str
    transcription_revision_id: str | None = None
    source_audio_sha256: str | None = None
    ref_text: str


class TtsSelectionSnapshot(BaseModel):
    target: TtsTargetSnapshot
    source: TtsSourceSnapshot


def _ordered_selection(
    values: list, requested_ids: list[str], id_attr: str, *, missing_code: str, missing_message: str
):
    requested = list(dict.fromkeys(str(value) for value in requested_ids if str(value)))
    by_id = {str(getattr(item, id_attr)): item for item in values}
    missing = [item_id for item_id in requested if item_id not in by_id]
    if missing:
        raise AppException(400, missing_code, missing_message, {"missing_ids": missing})
    requested_set = set(requested)
    ordered = [item for item in values if str(getattr(item, id_attr)) in requested_set]
    positions = [index for index, item in enumerate(values) if str(getattr(item, id_attr)) in requested_set]
    if positions and positions != list(range(positions[0], positions[-1] + 1)):
        return ordered, False
    return ordered, True


def _source_ref_text(draft: VideoLocalizationDraft, cue_ids: list[str], word_ids: list[str]) -> str:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    # The timeline cue text is the operator-reviewed ASR result.  Aligned words
    # can legitimately lag behind a spelling correction or a manual timing
    # adjustment, so they must never overwrite that final text at handoff.
    cue_text = " ".join((cue_by_id[cue_id].en_subtitle_text or "").strip() for cue_id in cue_ids).strip()
    if cue_text:
        return cue_text
    selected_word_ids = set(word_ids)
    words = (
        []
        if draft.transcription is None
        else [word for word in draft.transcription.words if word.word_id in selected_word_ids]
    )
    words.sort(key=lambda word: (word.start_ms, word.end_ms, word.word_id))
    if words:
        parts: list[str] = []
        previous_key = ""
        for word in words:
            value = word.text.strip()
            key = re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()
            if key and key == previous_key:
                continue
            parts.append(value)
            previous_key = key
        return " ".join(parts).strip()
    raw_parts = list(
        dict.fromkeys(
            (cue_by_id[cue_id].source_text_raw or "").strip()
            for cue_id in cue_ids
            if (cue_by_id[cue_id].source_text_raw or "").strip()
        )
    )
    return " ".join(raw_parts).strip()


def build_selection_snapshot(
    draft: VideoLocalizationDraft,
    request: TtsSelectionRequest,
) -> TtsSelectionSnapshot:
    ordered_subtitles = sorted(
        draft.localized_subtitles,
        key=lambda item: (item.start_ms, item.end_ms, item.subtitle_id),
    )
    targets, _target_contiguous = _ordered_selection(
        ordered_subtitles,
        request.target_subtitle_ids,
        "subtitle_id",
        missing_code="VIDEO_LOCALIZATION_TTS_TARGET_SUBTITLE_NOT_FOUND",
        missing_message="所选本土化字幕不存在，请重新选择",
    )
    if not targets:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_TARGET_SUBTITLES_MISSING", "请至少选择一条本土化字幕")
    spoken_by_id = {
        item.segment_id: item
        for item in draft.localized_spoken_segments
    }
    requested_spoken_ids = list(
        dict.fromkeys(
            item.spoken_segment_id
            for item in targets
            if item.spoken_segment_id
        )
    )
    missing_spoken_ids = [
        item for item in requested_spoken_ids if item not in spoken_by_id
    ]
    if missing_spoken_ids:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TTS_SPOKEN_SEGMENT_NOT_FOUND",
            "所选上屏字幕引用的中文台词已经失效，请重新生成本土化结果。",
            {"missing_ids": missing_spoken_ids},
        )
    target_text = "\n".join(
        (item.tts_text or item.text).strip()
        for item in targets
        if (item.tts_text or item.text).strip()
    )
    if not target_text:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_TEXT_MISSING", "所选本土化字幕没有配音台词")
    target_ids = [item.subtitle_id for item in targets]
    target_id_digest = hashlib.sha256("\0".join(target_ids).encode("utf-8")).hexdigest()[:12]
    segment_id = (
        target_ids[0]
        if len(target_ids) == 1
        else f"group_{target_ids[0]}_{target_ids[-1]}_{len(target_ids)}_{target_id_digest}"
    )
    source_ids = list(dict.fromkeys(request.source_cue_ids))
    if not source_ids:
        source_ids = list(
            dict.fromkeys(
                cue_id
                for item in targets
                for cue_id in (
                    item.source_cue_ids
                    if item.source_cue_ids
                    else (
                        [item.linked_cue_id]
                        if item.linked_cue_id
                        else []
                    )
                )
            )
        )
    if not source_ids:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_SOURCE_CUES_MISSING",
            "所选本土化字幕缺少原文字幕映射，请在原文轨选择参考语音",
        )

    ordered_cues = sorted(
        draft.cues,
        key=lambda item: (
            item.start_ms if item.start_ms is not None else 2**62,
            item.end_ms if item.end_ms is not None else 2**62,
            item.cue_id,
        ),
    )
    source_cues, source_contiguous = _ordered_selection(
        ordered_cues,
        source_ids,
        "cue_id",
        missing_code="VIDEO_LOCALIZATION_TTS_SOURCE_CUE_NOT_FOUND",
        missing_message="所选原文字幕不存在，请重新选择",
    )
    if not source_contiguous:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_SOURCE_NOT_CONTIGUOUS",
            "参考语音必须是同一段连续原声，不能自动把中间静音、演示或其他对白一起带入。",
            {"source_cue_ids": source_ids},
        )
    source_ids = [item.cue_id for item in source_cues]
    known_speaker_ids = {item.speaker_id for item in source_cues if item.speaker_id}
    if len(known_speaker_ids) > 1:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_SOURCE_CROSS_SPEAKER",
            "参考语音不能跨说话人，请为当前人物选择一段干净连续的原声。",
            {
                "source_cue_ids": source_ids,
                "speaker_ids": sorted(known_speaker_ids),
            },
        )
    # Diarization may leave a short neighbouring cue unlabelled. One known
    # speaker plus unlabelled cues is still one usable continuous reference;
    # only two conflicting known identities prove a cross-speaker selection.
    source_speaker_id = next(iter(known_speaker_ids), "unknown")
    invalid_ranges = [
        item.cue_id
        for item in source_cues
        if item.start_ms is None or item.end_ms is None or item.end_ms <= item.start_ms
    ]
    if invalid_ranges:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_SOURCE_RANGE_INVALID",
            "所选原文字幕的时间范围无效",
            {"source_cue_ids": invalid_ranges},
        )
    word_ids = list(dict.fromkeys(word_id for item in source_cues for word_id in item.source_word_ids))
    binding_payload = [
        {
            "subtitle_id": item.subtitle_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "text": item.tts_text or item.text,
            "source_cue_ids": item.source_cue_ids,
            "source_word_ids": item.source_word_ids,
        }
        for item in targets
    ]
    binding_fingerprint = hashlib.sha256(
        json.dumps(binding_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    transcription = draft.transcription
    return TtsSelectionSnapshot(
        target=TtsTargetSnapshot(
            subtitle_ids=target_ids,
            segment_id=segment_id,
            text=text_normalizer.normalize_tts_pronunciation(target_text),
            start_ms=min(item.start_ms for item in targets),
            end_ms=max(item.end_ms for item in targets),
            binding_fingerprint=binding_fingerprint,
        ),
        source=TtsSourceSnapshot(
            cue_ids=source_ids,
            word_ids=word_ids,
            start_ms=min(int(item.start_ms) for item in source_cues),
            end_ms=max(int(item.end_ms) for item in source_cues),
            speaker_id=source_speaker_id,
            transcription_revision_id=transcription.revision_id if transcription else None,
            source_audio_sha256=transcription.source_audio_sha256 if transcription else None,
            ref_text=_source_ref_text(draft, source_ids, word_ids),
        ),
    )
