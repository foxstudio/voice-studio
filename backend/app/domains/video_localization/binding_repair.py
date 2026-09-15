"""Pure, conservative repair of localized subtitle/source-word bindings."""

from __future__ import annotations

from collections.abc import Iterable

from app.domains.video_localization import localization_tracks, subtitles
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.errors import AppException
from app.schemas.video_localization_binding_repair import BindingRepairRequest


_TTS_FIELDS = (
    "tts_result_id", "tts_generation_id", "tts_audio_path", "tts_batch_task_id",
)


def repair_bindings(
    draft: VideoLocalizationDraft,
    request: BindingRepairRequest,
) -> VideoLocalizationDraft:
    """Move an existing consecutive word span between adjacent localized rows.

    Repository CAS and a live vocals-byte check belong to the application
    facade.  This pure function validates only the persisted graph and makes
    no attempt to regenerate, cancel, or reinterpret generated audio.
    """

    transcription = draft.transcription
    if transcription is None or transcription.revision_id != request.transcription_revision_id:
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_TRANSCRIPTION_CHANGED", "转写版本已变化，请刷新后重试。")
    if (
        transcription.source_track_id != "vocals"
        or transcription.alignment_source_track_id != "vocals"
    ):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_VOCALS_REQUIRED", "本次修复只能绑定当前分离人声音轨的转写。")
    if (
        transcription.source_audio_sha256 != request.audio_sha256
        or transcription.alignment_audio_sha256 != request.audio_sha256
    ):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_AUDIO_CHANGED", "保存的转写音轨指纹与本次修复不一致，请刷新后重试。")

    target_ids = [item.subtitle_id for item in request.bindings]
    subtitle_by_id = {item.subtitle_id: item for item in draft.localized_subtitles}
    if any(item_id not in subtitle_by_id for item_id in target_ids):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_SUBTITLE_NOT_FOUND", "要修复的本土化字幕已变化，请刷新后重试。")
    target_subtitles = [subtitle_by_id[item_id] for item_id in target_ids]
    _require_adjacent_subtitles(draft, target_ids)
    transcription_word_ids = [word.word_id for word in transcription.words]
    if len(transcription_word_ids) != len(set(transcription_word_ids)):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_ID_DUPLICATE", "当前转写包含重复来源词 ID，不能安全修复。")
    word_by_id = {word.word_id: word for word in transcription.words}
    source_cue_owners: dict[str, list[str]] = {}
    for cue in draft.cues:
        for word_id in cue.source_word_ids:
            source_cue_owners.setdefault(word_id, []).append(cue.cue_id)
    original = [word_id for subtitle in target_subtitles for word_id in subtitle.source_word_ids]
    desired = [word_id for binding in request.bindings for word_id in binding.source_word_ids]
    if not original or len(original) != len(set(original)) or desired != original:
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_COVERAGE_INVALID", "修复必须只在相邻字幕间原样移动连续来源词，不能增删、重复或改序。")
    if any(word_id not in word_by_id or word_id not in source_cue_owners for word_id in desired):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_SOURCE_WORD_NOT_FOUND", "有来源词不属于当前转写或字幕 cue，不能安全修复。")
    if any(len(source_cue_owners[word_id]) != 1 for word_id in desired):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_CUE_OWNERSHIP_INVALID", "来源词必须且只能属于一个当前 cue。")
    _require_transcription_continuity(transcription.words, desired)
    _require_no_foreign_overlap(draft, set(target_ids), set(desired))
    if all(
        subtitle.source_word_ids == binding.source_word_ids
        for subtitle, binding in zip(target_subtitles, request.bindings)
    ):
        return draft
    _reject_generated_dependencies(draft, target_subtitles)

    desired_by_id = {item.subtitle_id: item.source_word_ids for item in request.bindings}
    changed_ids = {
        subtitle.subtitle_id
        for subtitle in target_subtitles
        if subtitle.source_word_ids != desired_by_id[subtitle.subtitle_id]
    }
    if not changed_ids:
        return draft
    repaired = []
    for subtitle in draft.localized_subtitles:
        words = desired_by_id.get(subtitle.subtitle_id)
        if words is None or subtitle.subtitle_id not in changed_ids:
            repaired.append(subtitle)
            continue
        first, last = word_by_id[words[0]], word_by_id[words[-1]]
        if first.end_ms <= first.start_ms or last.end_ms <= last.start_ms or last.end_ms <= first.start_ms:
            _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_TIME_INVALID", "来源词时间无效，不能生成零宽字幕边界。")
        cue_ids = list(dict.fromkeys(source_cue_owners[word_id][0] for word_id in words))
        repaired.append(subtitle.model_copy(update={
            "source_word_ids": list(words),
            "source_cue_ids": cue_ids,
            "linked_cue_id": cue_ids[0],
            # The localized display track may intentionally retain a little
            # lead-in/out.  A binding repair only owns a boundary whose source
            # edge actually moved; resetting the other side would overwrite
            # that editorial timing and can create a new adjacent overlap.
            "start_ms": (
                first.start_ms
                if words[0] != subtitle.source_word_ids[0]
                else subtitle.start_ms
            ),
            "end_ms": (
                last.end_ms
                if words[-1] != subtitle.source_word_ids[-1]
                else subtitle.end_ms
            ),
        }))

    _require_changed_neighbor_timings(repaired, changed_ids)

    after = draft.model_copy(update={"localized_subtitles": repaired})
    reconciled = subtitles.with_editorial_subtitle_collection(draft, after, changed_ids)
    original_segments = {item.segment_id: item for item in draft.localized_spoken_segments}
    segment_words = _segment_words_from_subtitles(reconciled)
    restored_segments = []
    for segment in reconciled.localized_spoken_segments:
        original_segment = original_segments.get(segment.segment_id)
        if original_segment is None:
            restored_segments.append(segment)
            continue
        words = segment_words.get(segment.segment_id, segment.source_word_ids)
        update = {"text": original_segment.text}
        if not words or not original_segment.source_word_ids:
            _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_SEGMENT_EMPTY", "修复后配音段缺少来源词，不能安全保存。")
        update.update({
            "start_ms": (
                word_by_id[words[0]].start_ms
                if words[0] != original_segment.source_word_ids[0]
                else original_segment.start_ms
            ),
            "end_ms": (
                word_by_id[words[-1]].end_ms
                if words[-1] != original_segment.source_word_ids[-1]
                else original_segment.end_ms
            ),
        })
        restored_segments.append(segment.model_copy(update=update))
    return localization_tracks.invalidate_formal_quality_binding(
        reconciled.model_copy(update={"localized_spoken_segments": restored_segments})
    )


def _require_adjacent_subtitles(draft: VideoLocalizationDraft, target_ids: list[str]) -> None:
    positions = [next(index for index, item in enumerate(draft.localized_subtitles) if item.subtitle_id == item_id) for item_id in target_ids]
    if positions != list(range(positions[0], positions[0] + len(positions))):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_NOT_ADJACENT", "只能修复当前顺序相邻的本土化字幕。")


def _require_transcription_continuity(words: Iterable, desired: list[str]) -> None:
    positions = {word.word_id: index for index, word in enumerate(words)}
    selected = [positions[word_id] for word_id in desired]
    if selected != list(range(selected[0], selected[0] + len(selected))):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_ORDER_INVALID", "待迁移来源词必须是当前转写中的连续窗口。")


def _require_no_foreign_overlap(draft: VideoLocalizationDraft, target_ids: set[str], desired: set[str]) -> None:
    for subtitle in draft.localized_subtitles:
        if subtitle.subtitle_id in target_ids:
            continue
        if desired.intersection(subtitle.source_word_ids):
            _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_OVERLAP", "来源词同时被无关字幕占用，不能安全修复。")


def _require_changed_neighbor_timings(subtitles, changed_ids: set[str]) -> None:
    """Reject only new overlaps touching this repair, not legacy unrelated ones."""

    for left, right in zip(subtitles, subtitles[1:]):
        if left.subtitle_id not in changed_ids and right.subtitle_id not in changed_ids:
            continue
        if left.end_ms > right.start_ms:
            _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_SUBTITLE_OVERLAP", "修复后的相邻本土化字幕不能重叠。")


def _reject_generated_dependencies(draft: VideoLocalizationDraft, target_subtitles) -> None:
    target_ids = {item.subtitle_id for item in target_subtitles}
    target_segment_ids = {item.spoken_segment_id for item in target_subtitles if item.spoken_segment_id}
    target_cue_ids = {
        cue_id
        for item in target_subtitles
        for cue_id in item.source_cue_ids
    }
    related_ids = target_ids | target_segment_ids | target_cue_ids
    if any(any(getattr(item, field) for field in _TTS_FIELDS) for item in target_subtitles):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_TTS_EXISTS", "相关字幕已有生成结果，不能静默改绑。")
    if any(_references_target(task.model_dump(mode="python"), related_ids) for task in draft.tts_tasks):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_TTS_EXISTS", "相关字幕已有配音任务，不能静默改绑。")
    if any(_references_target(dict(candidate), related_ids) for candidate in draft.generated_candidates):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_CANDIDATE_EXISTS", "相关字幕已有候选结果，不能静默改绑。")
    if any(_references_target(dict(clip), related_ids) for clip in draft.timeline_clips):
        _reject("VIDEO_LOCALIZATION_BINDING_REPAIR_TTS_EXISTS", "相关字幕已有正式时间线片段，不能静默改绑。")


def _references_target(value, target_ids: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"subtitle_id", "segment_id", "spoken_segment_id", "cue_id"} and str(item) in target_ids:
                return True
            if key in {"subtitle_ids", "target_subtitle_ids", "source_cue_ids"} and isinstance(item, list) and target_ids.intersection(map(str, item)):
                return True
            if _references_target(item, target_ids):
                return True
    elif isinstance(value, list):
        return any(_references_target(item, target_ids) for item in value)
    return False


def _segment_words_from_subtitles(draft: VideoLocalizationDraft) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for subtitle in sorted(draft.localized_subtitles, key=lambda item: (item.start_ms, item.end_ms, item.subtitle_id)):
        if subtitle.spoken_segment_id:
            result.setdefault(subtitle.spoken_segment_id, []).extend(subtitle.source_word_ids)
    return {key: list(dict.fromkeys(value)) for key, value in result.items()}


def _reject(code: str, message: str) -> None:
    raise AppException(409, code, message)
