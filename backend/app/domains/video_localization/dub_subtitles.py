from __future__ import annotations

import hashlib
import json
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import (
    asr_pipeline,
    media_assets,
    subtitle_entry_timing,
    subtitle_exit_timing,
    subtitle_punctuation,
    timeline_audio_renderer,
    timeline_audio_sources,
    transcription,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationDubSubtitleCue,
    VideoLocalizationDubSubtitleDirtyRange,
    VideoLocalizationDubSubtitleDirtyScope,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.schemas import video_localization_dub_subtitle_step as step_contracts


from app.services import audio_tools, text_normalizer

SOURCE_CHANGED_QUALITY_FLAG = "stale:source-changed"


DEFAULT_ENGINE_ID = "qwen3-asr-mlx"
MIN_SEMANTIC_CUE_DURATION_MS = 800
SHORT_CUE_MERGE_GAP_MS = 320
MAX_REFERENCE_CORRECTION_TOKENS = 4
MAX_DISPLAY_FORM_TOKENS = 24
_PROOFREAD_TOKEN_PATTERN = re.compile(
    r"\d+(?:[.,:]\d+)+|"
    r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|"
    r"[@#%&+/°℃]|"
    r"[\u3400-\u9fff]"
)
_REFERENCE_SEMANTIC_SYMBOLS = frozenset("@#%&+/°℃")
_CHINESE_NUMBER_CHARACTERS = frozenset(
    "零〇一二两三四五六七八九十百千万亿点"
)
_DISPLAY_UNIT_SPOKEN_FORMS = (
    (re.compile(r"(?i)(?<![A-Za-z])km\s*/\s*h(?![A-Za-z])"), "公里每小时"),
    (re.compile(r"(?i)(?<![A-Za-z])m\s*/\s*s(?![A-Za-z])"), "米每秒"),
    (re.compile(r"(?i)(?<![A-Za-z])kwh(?![A-Za-z])"), "千瓦时"),
    (re.compile(r"(?i)(?<![A-Za-z])gbps(?![A-Za-z])"), "吉比特每秒"),
    (re.compile(r"(?i)(?<![A-Za-z])mbps(?![A-Za-z])"), "兆比特每秒"),
    (re.compile(r"(?i)(?<![A-Za-z])kbps(?![A-Za-z])"), "千比特每秒"),
    (re.compile(r"(?i)(?<![A-Za-z])ghz(?![A-Za-z])"), "吉赫兹"),
    (re.compile(r"(?i)(?<![A-Za-z])mhz(?![A-Za-z])"), "兆赫兹"),
    (re.compile(r"(?i)(?<![A-Za-z])khz(?![A-Za-z])"), "千赫兹"),
    (re.compile(r"(?i)(?<![A-Za-z])fps(?![A-Za-z])"), "帧每秒"),
    (re.compile(r"(?i)(?<![A-Za-z])hz(?![A-Za-z])"), "赫兹"),
    (re.compile(r"(?i)(?<![A-Za-z])ml(?![A-Za-z])"), "毫升"),
    (re.compile(r"(?i)(?<![A-Za-z])kg(?![A-Za-z])"), "千克"),
    (re.compile(r"(?i)(?<![A-Za-z])mg(?![A-Za-z])"), "毫克"),
    (re.compile(r"(?i)(?<![A-Za-z])km(?![A-Za-z])"), "公里"),
    (re.compile(r"(?i)(?<![A-Za-z])cm(?![A-Za-z])"), "厘米"),
    (re.compile(r"(?i)(?<![A-Za-z])mm(?![A-Za-z])"), "毫米"),
    (re.compile(r"(?i)(?<![A-Za-z])ms(?![A-Za-z])"), "毫秒"),
)


class _DubSubtitleClip(BaseModel):
    """Runtime view of one prepared synthesized-dub clip."""

    model_config = ConfigDict(extra="forbid")

    clip_id: str = Field(min_length=1)
    dub_lane: int = Field(ge=0)
    audio_sha256: str = Field(min_length=64, max_length=64)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    reference_ids: list[str] = Field(default_factory=list)
    reference_text: str = ""
    speaker_id: str | None = None
    speaker_known: bool = False


class _DubSubtitleCorrection(BaseModel):
    """Bounded diagnostic showing where ASR differed from localized text."""

    model_config = ConfigDict(extra="forbid")

    before: str
    after: str
    kind: Literal["added", "removed", "changed"]


def validate_prerequisites(draft: VideoLocalizationDraft) -> None:
    _audible_dub_clips(draft)


def freeze_workflow_input(
    draft: VideoLocalizationDraft,
    *,
    generation_text_by_task_id: Mapping[str, str] | None = None,
    regeneration_mode: Literal["auto", "full"] = "auto",
) -> step_contracts.DubSubtitleWorkflowInput:
    clips = _audible_dub_clips(
        draft,
        allow_empty=(
            bool(draft.dub_subtitles)
            and draft.dub_subtitle_dirty_scope is not None
        ),
    )
    video_duration_ms = int(draft.source_media.duration_ms or 0)
    video_frame_rate = (
        float(draft.source_media.frame_rate)
        if draft.source_media.frame_rate
        else None
    )
    if video_duration_ms <= 0:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SOURCE_DURATION_REQUIRED",
            "源视频缺少有效时长，无法生成完整配音音频。",
        )
    subtitle_by_id = {
        item.subtitle_id: item for item in draft.localized_subtitles
    }
    cue_by_id = {item.cue_id: item for item in draft.cues}
    display_text_by_id = {
        item.subtitle_id: _localized_display_text(item.text)
        for item in draft.localized_subtitles
    }
    generated_text_by_task_id = {
        str(item.generation_task_id): str(item.text or "").strip()
        for item in draft.tts_tasks
        if item.generation_task_id and str(item.text or "").strip()
    }
    generated_text_by_task_id.update(
        {
            str(task_id): str(text or "").strip()
            for task_id, text in (
                generation_text_by_task_id or {}
            ).items()
            if str(task_id or "").strip() and str(text or "").strip()
        }
    )
    frozen: list[step_contracts.DubSubtitleSourceClip] = []
    ordered_references: dict[
        str,
        step_contracts.DubSubtitleTextReference,
    ] = {}
    for clip in clips:
        path = Path(str(clip["audio_path"]))
        duration_ms = audio_tools.probe_audio_duration_ceil_ms(path)
        start_ms = _clip_ms(clip, "start_ms", 0)
        source_start_ms = _clip_ms(clip, "source_start_ms", 0)
        source_end_ms = _clip_ms(
            clip,
            "source_end_ms",
            duration_ms,
        )
        end_ms = _clip_ms(
            clip,
            "end_ms",
            start_ms + max(0, source_end_ms - source_start_ms),
        )
        source_end_ms = min(
            source_end_ms,
            source_start_ms + max(0, end_ms - start_ms),
            duration_ms,
        )
        if (
            end_ms <= start_ms
            or source_end_ms <= source_start_ms
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_RANGE_INVALID",
                (
                    f"合成配音片段 {clip.get('clip_id') or ''} "
                    "没有可识别的有效音频范围。"
                ),
            )
        if end_ms > video_duration_ms:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_OUTSIDE_VIDEO",
                (
                    f"合成配音片段 {clip.get('clip_id') or ''} "
                    "超出源视频时长，无法生成等长完整音频。"
                ),
            )
        playable_duration_ms = source_end_ms - source_start_ms
        end_ms = start_ms + playable_duration_ms
        source_end_ms = source_start_ms + playable_duration_ms
        target_ids = [
            str(value)
            for value in (
                clip.get("target_subtitle_ids")
                or (
                    [clip.get("subtitle_id")]
                    if clip.get("subtitle_id")
                    else []
                )
            )
            if str(value or "").strip()
        ]
        references = [
            subtitle_by_id[subtitle_id]
            for subtitle_id in target_ids
            if subtitle_id in subtitle_by_id
        ]
        if not references:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REFERENCE_MISSING",
                (
                    f"合成配音片段 {clip.get('clip_id') or ''} "
                    "缺少稳定的本土化字幕关联，不能按字幕时间猜测参考文字。"
                ),
            )
        generation_task_id = str(
            clip.get("task_id")
            or clip.get("generation_id")
            or ""
        ).strip()
        # The adopted timeline clip is the durable production authority. A
        # live task queue may disappear after restart or retain a superseded
        # request, so it can only fill legacy clips that did not freeze the
        # accepted target text at placement time.
        generated_text = _localized_display_text(
            str(clip.get("tts_target_text") or "")
        ) or _localized_display_text(
            generated_text_by_task_id.get(generation_task_id, "")
        )
        if generated_text:
            reference_ids = [
                (
                    f"generation-task:{generation_task_id}"
                    if generation_task_id
                    else f"timeline-clip:{clip['clip_id']}"
                )
            ]
            canonical_display_text = " ".join(
                display_text_by_id[item.subtitle_id]
                for item in references
                if display_text_by_id[item.subtitle_id]
            ).strip()
            ordered_references.setdefault(
                reference_ids[0],
                step_contracts.DubSubtitleTextReference(
                    subtitle_id=reference_ids[0],
                    text=generated_text,
                    display_text=(
                        canonical_display_text or generated_text
                    ),
                ),
            )
        else:
            reference_ids = []
            for item in references:
                text = display_text_by_id[item.subtitle_id]
                if not text:
                    continue
                reference_ids.append(item.subtitle_id)
                ordered_references.setdefault(
                    item.subtitle_id,
                    step_contracts.DubSubtitleTextReference(
                        subtitle_id=item.subtitle_id,
                        text=text,
                        display_text=text,
                    ),
                )
        source_cue_ids = [
            str(value)
            for value in clip.get("source_cue_ids") or []
            if str(value or "").strip()
        ]
        if not source_cue_ids:
            source_cue_ids = list(
                dict.fromkeys(
                    cue_id
                    for item in references
                    for cue_id in (
                        item.source_cue_ids
                        or (
                            [item.linked_cue_id]
                            if item.linked_cue_id
                            else []
                        )
                    )
                )
            )
        speaker_ids = {
            cue_by_id[cue_id].speaker_id
            for cue_id in source_cue_ids
            if cue_id in cue_by_id
            and cue_by_id[cue_id].speaker_id
        }
        frozen.append(
            step_contracts.DubSubtitleSourceClip(
                clip_id=str(clip["clip_id"]),
                dub_lane=max(0, int(clip.get("dub_lane") or 0)),
                audio_sha256=media_assets.file_sha256(path),
                timeline_start_ms=start_ms,
                timeline_end_ms=end_ms,
                source_start_ms=source_start_ms,
                source_end_ms=source_end_ms,
                reference_ids=reference_ids,
                speaker_id=(
                    next(iter(speaker_ids))
                    if len(speaker_ids) == 1
                    else None
                ),
            )
        )
    frozen_references = list(ordered_references.values())
    if not frozen_references and frozen:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_REFERENCE_MISSING",
            "可听合成配音没有对应的本土化台词，无法校对字幕文字。",
        )
    source_revision = _source_revision(
        draft,
        frozen,
        frozen_references,
        video_frame_rate=video_frame_rate,
    )
    scope = _freeze_regeneration_scope(
        draft,
        frozen,
        regeneration_mode=regeneration_mode,
    )
    selected_clip_ids = set(scope.affected_clip_ids)
    selected_ranges = [
        (item.start_ms, item.end_ms)
        for item in scope.affected_ranges
    ]
    selected_clips = (
        frozen
        if scope.mode == "full"
        else [
            item
            for item in frozen
            if (
                item.clip_id in selected_clip_ids
                or any(
                    item.timeline_start_ms < end_ms
                    and item.timeline_end_ms > start_ms
                    for start_ms, end_ms in selected_ranges
                )
            )
        ]
    )
    selected_reference_ids = {
        reference_id
        for clip in selected_clips
        for reference_id in clip.reference_ids
    }
    selected_references = [
        item
        for item in frozen_references
        if item.subtitle_id in selected_reference_ids
    ]
    return step_contracts.DubSubtitleWorkflowInput(
        source_revision=source_revision,
        video_frame_rate=video_frame_rate,
        prepare_track=step_contracts.DubSubtitlePrepareTrackInput(
            timeline_duration_ms=video_duration_ms,
            clips=selected_clips,
        ),
        references=selected_references,
        regeneration_scope=scope,
    )


def resolve_prepare_audio_paths(
    draft: VideoLocalizationDraft,
    request: step_contracts.DubSubtitleWorkflowInput,
) -> dict[str, Path]:
    resolved_paths = timeline_audio_sources.resolve_dub_clip_audio_paths(
        draft
    )
    output: dict[str, Path] = {}
    for clip in request.prepare_track.clips:
        path = resolved_paths.get(clip.clip_id)
        if (
            path is None
            or not path.is_file()
            or media_assets.file_sha256(path) != clip.audio_sha256
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_SOURCE_CHANGED",
                (
                    f"合成配音片段 {clip.clip_id} 已变化，"
                    "请基于当前时间线重新识别。"
                ),
            )
        output[clip.clip_id] = path
    return output


def _freeze_regeneration_scope(
    draft: VideoLocalizationDraft,
    clips: list[step_contracts.DubSubtitleSourceClip],
    *,
    regeneration_mode: Literal["auto", "full"],
) -> step_contracts.DubSubtitleRegenerationScope:
    """Choose the smallest safe audio set without weakening the source fence."""

    if regeneration_mode == "full":
        return step_contracts.DubSubtitleRegenerationScope(
            mode="full",
            reason="explicit_full",
            affected_clip_ids=[item.clip_id for item in clips],
            affected_ranges=[
                step_contracts.DubSubtitleAffectedRange(
                    start_ms=item.timeline_start_ms,
                    end_ms=item.timeline_end_ms,
                )
                for item in clips
            ],
        )
    if not draft.dub_subtitles:
        return step_contracts.DubSubtitleRegenerationScope(
            mode="full",
            reason="no_prior_subtitles",
            affected_clip_ids=[item.clip_id for item in clips],
            affected_ranges=[
                step_contracts.DubSubtitleAffectedRange(
                    start_ms=item.timeline_start_ms,
                    end_ms=item.timeline_end_ms,
                )
                for item in clips
            ],
        )
    dirty = draft.dub_subtitle_dirty_scope
    if dirty is None or (
        not dirty.affected_clip_ids and not dirty.affected_ranges
    ):
        # A legacy result has no stored replacement boundary.  Auto mode must
        # never turn that uncertainty into a whole-track overwrite: an edited
        # caption outside the unknown source change is otherwise lost.
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_UNKNOWN",
            "这份历史配音字幕没有保存变更范围；请明确选择全量重新生成。",
        )
    return step_contracts.DubSubtitleRegenerationScope(
        mode="incremental",
        reason="dirty_scope",
        affected_clip_ids=list(dirty.affected_clip_ids),
        affected_ranges=[
            step_contracts.DubSubtitleAffectedRange(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            for item in dirty.affected_ranges
        ],
        replaceable_subtitle_fingerprints=dict(
            dirty.replaceable_subtitle_fingerprints
        ),
    )


def prepare_track(
    request: step_contracts.DubSubtitlePrepareTrackInput,
    *,
    audio_paths: Mapping[str, Path],
    artifact_id: str,
    artifact_path: Path,
) -> step_contracts.DubSubtitlePrepareTrackOutput:
    runtime_clips = _runtime_clips_from_source_clips(request.clips)
    with _render_timeline_track(
        runtime_clips,
        audio_paths,
        timeline_duration_ms=request.timeline_duration_ms,
        output_path=artifact_path,
        remove_on_exit=False,
    ) as rendered:
        if rendered.path is None:
            raise RuntimeError("完整配音音轨未生成。")
        return step_contracts.DubSubtitlePrepareTrackOutput(
            audio=step_contracts.DubSubtitlePreparedAudio(
                artifact_id=artifact_id,
                audio_sha256=rendered.audio_sha256,
                duration_ms=rendered.duration_ms,
            ),
            clip_count=len(request.clips),
        )


def transcribe_track(
    request: step_contracts.DubSubtitleTranscribeTrackInput,
    *,
    audio_path: Path,
    is_cancelled: Callable[[], bool] | None = None,
) -> step_contracts.DubSubtitleTranscribeTrackOutput:
    ensure_prepared_audio_available(
        request.audio,
        audio_path=audio_path,
    )
    raw = asr_pipeline.DEFAULT_ASR_PIPELINE.run_raw_asr(
        transcription.TranscribeRawInput(
            audio_path=str(audio_path),
            audio_sha256=(
                request.audio.audio_sha256
            ),
            engine_id=request.engine_id,
            source_track_id="dub",
            requested_language=request.requested_language,
            duration_ms=request.audio.duration_ms,
            context_terms=[],
        ),
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled
        ),
    )
    _ensure_active(is_cancelled)
    if (
        raw.quality_summary.status == "failed"
        or not raw.raw_text.strip()
        or not raw.segments
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLES_EMPTY",
            "整条合成配音已识别，但没有得到可用文字。",
        )
    chunks = [
        step_contracts.DubSubtitleTranscriptChunk(
            segment_id=item.segment_id,
            audio_window_start_ms=item.start_ms,
            audio_window_end_ms=item.end_ms,
            text=str(
                item.raw_text or item.corrected_text or ""
            ).strip(),
        )
        for item in raw.segments
        if str(
            item.raw_text or item.corrected_text or ""
        ).strip()
        and item.end_ms > item.start_ms
    ]
    if request.audible_clips:
        chunks = [
            chunk
            for chunk in chunks
            if any(
                clip.timeline_end_ms
                > chunk.audio_window_start_ms
                and clip.timeline_start_ms
                < chunk.audio_window_end_ms
                for clip in request.audible_clips
            )
        ]

    output = step_contracts.DubSubtitleTranscribeTrackOutput(
        audio_sha256=request.audio.audio_sha256,
        engine_id=request.engine_id,
        language=raw.language,
        chunks=chunks,
        quality_status=raw.quality_summary.status,
        quality_flags=list(
            raw.quality_summary.warning_codes
        ),
    )
    if not output.chunks:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLES_EMPTY",
            "整条合成配音已识别，但没有得到带时间的听写片段。",
        )
    return output


def proofread_text(
    request: step_contracts.DubSubtitleProofreadTextInput,
) -> step_contracts.DubSubtitleProofreadTextOutput:
    reference_text = "。".join(
        item.text for item in request.references
    )
    raw_text = "".join(item.text for item in request.chunks)
    corrected_text, stats = _proofread_document_text(
        raw_text,
        reference_text,
    )
    corrected_text = _canonicalize_reference_display_forms(
        corrected_text,
        request.references,
    )
    raw_cues = _cues_from_asr_segments_exact(
        [
            VideoLocalizationTranscriptSegment(
                segment_id=item.segment_id,
                start_ms=item.audio_window_start_ms,
                end_ms=item.audio_window_end_ms,
                raw_text=item.text,
                corrected_text=item.text,
            )
            for item in request.chunks
        ]
    )
    corrected_cues = _proofread_asr_cues(
        raw_cues,
        corrected_text,
    )
    raw_by_id = {
        item.cue_id: str(item.en_subtitle_text or "").strip()
        for item in raw_cues
    }
    cues = [
        step_contracts.DubSubtitleProofreadChunk(
            cue_id=item.cue_id,
            audio_window_start_ms=int(item.start_ms or 0),
            audio_window_end_ms=int(item.end_ms or 0),
            raw_text=raw_by_id.get(
                item.cue_id,
                str(item.source_text_raw or item.en_subtitle_text or ""),
            ),
            text=str(item.en_subtitle_text or "").strip(),
        )
        for item in corrected_cues
        if str(item.en_subtitle_text or "").strip()
    ]
    if not cues:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLES_EMPTY",
            "整轨听写校对后没有得到可用字幕文字。",
        )
    final_text = "".join(item.text for item in cues)
    corrections = _asr_reference_corrections(raw_text, final_text)
    return step_contracts.DubSubtitleProofreadTextOutput(
        chunks=cues,
        corrections=[
            step_contracts.DubSubtitleTextCorrection(
                before=item.before,
                after=item.after,
                kind=item.kind,
            )
            for item in corrections
        ],
        unmatched_asr_char_count=stats[
            "unmatched_asr_char_count"
        ],
        unmatched_reference_char_count=stats[
            "unmatched_localized_char_count"
        ],
    )


def align_words(
    request: step_contracts.DubSubtitleAlignWordsInput,
    *,
    audio_path: Path,
    is_cancelled: Callable[[], bool] | None = None,
) -> step_contracts.DubSubtitleAlignWordsOutput:
    ensure_prepared_audio_available(
        request.audio,
        audio_path=audio_path,
    )
    _ensure_active(is_cancelled)
    transcript_segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=item.cue_id,
            start_ms=item.audio_window_start_ms,
            end_ms=item.audio_window_end_ms,
            raw_text=item.raw_text,
            corrected_text=item.text,
        )
    for item in request.chunks
    ]
    try:
        aligned_words, alignment_meta = (
            transcription.align_segments_strict(
                audio_path,
                transcript_segments,
                language=(
                    request.language
                ),
                max_duration_ms=(
                    request.audio.duration_ms
                ),
            )
        )
        aligned_words, zero_duration_repair_count = (
            subtitle_entry_timing.repair_zero_duration_word_ends(
                audio_path,
                aligned_words,
            )
        )
        if zero_duration_repair_count:
            alignment_meta["zero_duration_acoustic_repair_count"] = (
                zero_duration_repair_count
            )
            alignment_meta["quality_flags"] = list(
                dict.fromkeys(
                    [
                        *alignment_meta.get("quality_flags", []),
                        "alignment_zero_duration_acoustically_repaired",
                    ]
                )
            )
    except Exception as exc:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
            (
                "合成配音文字没有取得完整的真实声学时间，"
                f"因此没有生成或保存字幕：{exc}"
            ),
        ) from exc
    _ensure_active(is_cancelled)
    if any(
        item.timing_source != "forced_aligner"
        for item in aligned_words
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
            "声学对齐返回了非真实发音来源的时间，因此没有生成字幕。",
        )
    _validate_strict_alignment_output(
        request.chunks,
        aligned_words,
    )
    subtitle_entry_by_word_id = (
        subtitle_entry_timing.detect_subtitle_entries(
            audio_path,
            aligned_words,
            frame_rate=request.video_frame_rate,
        )
    )
    return step_contracts.DubSubtitleAlignWordsOutput(
        audio_sha256=request.audio.audio_sha256,
        words=[
            step_contracts.DubSubtitleAlignedWord(
                word_id=item.word_id,
                cue_id=item.segment_id,
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                timing_source=item.timing_source,
            )
            for item in aligned_words
        ],
        subtitle_entry_by_word_id=subtitle_entry_by_word_id,
        alignment_call_count=int(
            alignment_meta.get("alignment_call_count") or 0
        ),
    )


def _validate_strict_alignment_output(
    chunks: list[step_contracts.DubSubtitleProofreadChunk],
    words: list[VideoLocalizationAlignedWord],
) -> None:
    """Enforce the complete acoustic-word postcondition at this boundary."""

    words_by_cue: dict[str, list[VideoLocalizationAlignedWord]] = {}
    for word in words:
        words_by_cue.setdefault(word.segment_id, []).append(word)
    expected_cue_ids = {chunk.cue_id for chunk in chunks}
    if set(words_by_cue) != expected_cue_ids:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
            "声学对齐没有完整覆盖全部听写音频块，因此没有生成字幕。",
        )
    for chunk in chunks:
        aligned = words_by_cue[chunk.cue_id]
        expected_tokens = transcription.display_tokens(chunk.text)
        if [word.text for word in aligned] != expected_tokens:
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                (
                    f"听写音频块 {chunk.cue_id} 的声学字词"
                    "没有完整覆盖校对文字，因此没有生成字幕。"
                ),
            )
        previous_start_ms = chunk.audio_window_start_ms
        for word in aligned:
            if (
                word.start_ms < chunk.audio_window_start_ms
                or word.end_ms > chunk.audio_window_end_ms
                # Qwen uses point anchors for some internal one-character
                # tokens. They are valid acoustic onsets as long as order is
                # monotonic; displayed subtitle spans still require a real
                # positive duration at the segmentation boundary below.
                or word.end_ms < word.start_ms
                or word.start_ms < previous_start_ms
            ):
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                    (
                        f"听写音频块 {chunk.cue_id} 的声学字词"
                        "存在越界或倒序时间，因此没有生成字幕。"
                    ),
                )
            previous_start_ms = word.start_ms


def segment_subtitles(
    request: step_contracts.DubSubtitleSegmentSubtitlesInput,
) -> step_contracts.DubSubtitleSegmentSubtitlesOutput:
    aligned_words = [
        VideoLocalizationAlignedWord(
            word_id=item.word_id,
            segment_id=item.cue_id,
            text=item.text,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            timing_confidence="high",
            timing_source=item.timing_source,
        )
        for item in request.words
    ]
    refined = _cues_from_strict_alignment(
        request.chunks,
        aligned_words,
        clips=request.clips,
        subtitle_entry_by_word_id=(
            request.subtitle_entry_by_word_id
        ),
    )
    subtitles = _candidate_subtitles(
        refined,
        clips=request.clips,
        source_audio_sha256=request.audio_sha256,
        asr_requires_review=request.asr_requires_review,
        frame_rate=request.video_frame_rate,
        media_duration_ms=request.video_duration_ms,
    )
    return step_contracts.DubSubtitleSegmentSubtitlesOutput(
        subtitles=[
            step_contracts.DubSubtitleCandidateCue.model_validate(
                item.model_dump(mode="json")
            )
            for item in subtitles
        ]
    )


def _runtime_clips_from_source_clips(
    clips: list[step_contracts.DubSubtitleSourceClip],
) -> list[_DubSubtitleClip]:
    return [
        _DubSubtitleClip(
            clip_id=item.clip_id,
            dub_lane=item.dub_lane,
            audio_sha256=item.audio_sha256,
            timeline_start_ms=item.timeline_start_ms,
            timeline_end_ms=item.timeline_end_ms,
            source_start_ms=item.source_start_ms,
            source_end_ms=item.source_end_ms,
            reference_ids=list(item.reference_ids),
            speaker_id=item.speaker_id,
            speaker_known=item.speaker_id is not None,
        )
        for item in clips
    ]


def ensure_prepared_audio_available(
    prepared: step_contracts.DubSubtitlePreparedAudio,
    *,
    audio_path: Path,
) -> None:
    if (
        not audio_path.is_file()
        or media_assets.file_sha256(audio_path)
        != prepared.audio_sha256
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ARTIFACT_CHANGED",
            "完整配音音轨已丢失或变化，请重新执行准备音轨子流程。",
        )


def _candidate_subtitles(
    cues: list[VideoLocalizationCue],
    *,
    clips: list[step_contracts.DubSubtitleTimelineClip],
    source_audio_sha256: str,
    asr_requires_review: bool,
    frame_rate: float | None = None,
    media_duration_ms: int | None = None,
) -> list[VideoLocalizationDubSubtitleCue]:
    subtitles: list[VideoLocalizationDubSubtitleCue] = []
    for cue_index, cue in enumerate(cues, start=1):
        raw_text = str(cue.en_subtitle_text or "")
        number_text = (
            subtitle_punctuation.normalize_display_subtitle_numbers(raw_text)
        )
        text = subtitle_punctuation.normalize_display_subtitle_punctuation(
            raw_text
        ).strip()
        if not text:
            continue
        start_ms = max(0, int(cue.start_ms or 0))
        end_ms = max(start_ms + 1, int(cue.end_ms or 0))
        overlaps = _overlapping_clips(clips, start_ms, end_ms)
        timing_outside_clip = not overlaps
        source_clip_ids = [item.clip_id for item in overlaps]
        speaker_ids = {
            item.speaker_id
            for item in overlaps
            if item.speaker_id
        }
        speaker_known = (
            len(speaker_ids) == 1
            and all(item.speaker_id is not None for item in overlaps)
        )
        cue_flags = list(cue.quality_flags)
        if number_text != raw_text:
            cue_flags.append("display:number-form-normalized")
        if not speaker_known:
            cue_flags.append("speaker_metadata_missing")
        if timing_outside_clip:
            cue_flags.append("asr_timing_outside_dub_clip")
        if asr_requires_review:
            cue_flags.append("asr_quality_warning")
        subtitles.append(
            VideoLocalizationDubSubtitleCue(
                subtitle_id=_timeline_subtitle_id(
                    source_clip_ids,
                    cue_index,
                    start_ms,
                ),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=(
                    next(iter(speaker_ids))
                    if speaker_known
                    else None
                ),
                source_clip_ids=source_clip_ids,
                dub_lanes=sorted(
                    {item.dub_lane for item in overlaps}
                ),
                source_audio_sha256=source_audio_sha256,
                needs_review=(
                    not speaker_known
                    or asr_requires_review
                    or "timing_review_required" in cue_flags
                    or "segmentation_review_required" in cue_flags
                    or "proofread:large-difference" in cue_flags
                    or timing_outside_clip
                ),
                quality_flags=list(dict.fromkeys(cue_flags)),
            )
        )
    subtitles.sort(
        key=lambda item: (
            item.start_ms,
            item.end_ms,
            item.dub_lanes[0] if item.dub_lanes else -1,
            item.subtitle_id,
        )
    )
    resolved = subtitle_exit_timing.resolve_display_exit_times(
        [
            subtitle_exit_timing.SubtitleTimingSpan(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                lane_ids=frozenset(
                    f"dub:{lane}" for lane in item.dub_lanes
                ),
            )
            for item in subtitles
        ],
        frame_rate=frame_rate,
        media_duration_ms=media_duration_ms,
    )
    return [
        item.model_copy(
            update={
                "end_ms": span.end_ms,
                "quality_flags": (
                    list(
                        dict.fromkeys(
                            [
                                *item.quality_flags,
                                "timing:display-exit-extended",
                            ]
                        )
                    )
                    if span.end_ms != item.end_ms
                    else item.quality_flags
                ),
            }
        )
        for item, span in zip(subtitles, resolved)
    ]


def ensure_prepare_source_unchanged(
    latest: VideoLocalizationDraft,
    request: step_contracts.DubSubtitleWorkflowInput,
    *,
    generation_text_by_task_id: Mapping[str, str] | None = None,
) -> None:
    current = freeze_workflow_input(
        latest,
        generation_text_by_task_id=generation_text_by_task_id,
    )
    if current.source_revision != request.source_revision:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_SOURCE_CHANGED",
            (
                "识别期间合成配音、轨道静音状态或参考台词发生了变化，"
                "因此没有写入旧结果。请基于当前时间线重新识别。"
            ),
        )


def merge_generated_subtitles(
    latest: VideoLocalizationDraft,
    request: step_contracts.DubSubtitleCommitInput,
) -> tuple[
    VideoLocalizationDraft,
    step_contracts.DubSubtitleCommitOutput,
]:
    generated = [
        VideoLocalizationDubSubtitleCue.model_validate(
            item.model_dump(mode="json")
        )
        for item in request.subtitles
    ]
    scope = request.regeneration_scope
    if scope is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_UNKNOWN",
            "旧字幕提交输入没有保存替换范围，不能安全写入；请重新生成当前步骤。",
        )
    if scope.mode == "incremental":
        _ensure_generated_subtitles_within_scope(generated, scope)
    generated = _recover_exact_cqc_clip_omissions(
        latest,
        generated,
        source_revision=request.source_revision,
        eligible_clip_ids=(
            set(scope.affected_clip_ids)
            if scope.mode == "incremental"
            else None
        ),
    )
    preserved, preserved_manual = _preserve_unreplaced_subtitles(
        latest,
        scope,
    )
    generated = _without_manual_conflicts(
        generated,
        preserved,
        scope=scope,
    )
    subtitles = sorted(
        [*preserved, *generated],
        key=lambda item: (
            item.start_ms,
            item.end_ms,
            item.dub_lanes[0] if item.dub_lanes else -1,
            item.subtitle_id,
        ),
    )
    merged = latest.model_copy(
        update={
            "dub_subtitles": subtitles,
            "dub_subtitle_source_revision": request.source_revision,
            "dub_subtitle_dirty_scope": None,
        }
    )
    return (
        merged,
        step_contracts.DubSubtitleCommitOutput(
            saved_source_revision=request.source_revision,
            saved_subtitle_count=len(subtitles),
            generated_subtitle_count=len(generated),
            preserved_subtitle_count=len(preserved),
            preserved_manual_subtitle_count=preserved_manual,
        ),
    )


def _ensure_generated_subtitles_within_scope(
    subtitles: list[VideoLocalizationDubSubtitleCue],
    scope: step_contracts.DubSubtitleRegenerationScope,
) -> None:
    affected_clip_ids = set(scope.affected_clip_ids)
    affected_ranges = [
        (item.start_ms, item.end_ms) for item in scope.affected_ranges
    ]
    invalid = [
        item.subtitle_id
        for item in subtitles
        if not _cue_in_regeneration_scope(
            item,
            affected_clip_ids=affected_clip_ids,
            affected_ranges=affected_ranges,
        )
    ]
    if invalid:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_CHANGED",
            "本次只允许写入编辑后受影响的配音字幕范围。",
            {"subtitle_ids": invalid},
        )


def _preserve_unreplaced_subtitles(
    latest: VideoLocalizationDraft,
    scope: step_contracts.DubSubtitleRegenerationScope,
) -> tuple[list[VideoLocalizationDubSubtitleCue], int]:
    if scope.mode == "full":
        return [], 0
    affected_clip_ids = set(scope.affected_clip_ids)
    affected_ranges = [
        (item.start_ms, item.end_ms) for item in scope.affected_ranges
    ]
    fingerprints = scope.replaceable_subtitle_fingerprints
    preserved: list[VideoLocalizationDubSubtitleCue] = []
    manual_count = 0
    for cue in latest.dub_subtitles:
        in_scope = _cue_in_regeneration_scope(
            cue,
            affected_clip_ids=affected_clip_ids,
            affected_ranges=affected_ranges,
        )
        expected_fingerprint = fingerprints.get(cue.subtitle_id)
        if in_scope and expected_fingerprint == _caption_fingerprint(cue):
            # This is the exact stale derived cue that was present when the
            # audio scope froze.  The candidate result may replace it.
            continue
        preserved.append(cue)
        if in_scope:
            manual_count += 1
    return preserved, manual_count


def _without_manual_conflicts(
    generated: list[VideoLocalizationDubSubtitleCue],
    preserved: list[VideoLocalizationDubSubtitleCue],
    *,
    scope: step_contracts.DubSubtitleRegenerationScope,
) -> list[VideoLocalizationDubSubtitleCue]:
    if scope.mode == "full" or not preserved:
        return generated
    scope_ids = set(scope.replaceable_subtitle_fingerprints)
    manual = [item for item in preserved if item.subtitle_id in scope_ids]
    # A cue added after the job began has no frozen fingerprint but is still a
    # user-owned edit if it occupies the regeneration range.
    affected_clip_ids = set(scope.affected_clip_ids)
    affected_ranges = [
        (item.start_ms, item.end_ms) for item in scope.affected_ranges
    ]
    manual.extend(
        item
        for item in preserved
        if item.subtitle_id not in scope_ids
        and _cue_in_regeneration_scope(
            item,
            affected_clip_ids=affected_clip_ids,
            affected_ranges=affected_ranges,
        )
    )
    manual_ids = {item.subtitle_id for item in manual}
    return [
        candidate
        for candidate in generated
        if candidate.subtitle_id not in manual_ids
        and not any(
            _cue_in_regeneration_scope(
                candidate,
                affected_clip_ids=set(manual_cue.source_clip_ids),
                affected_ranges=[
                    (manual_cue.start_ms, manual_cue.end_ms)
                ],
            )
            for manual_cue in manual
        )
    ]


def _recover_exact_cqc_clip_omissions(
    draft: VideoLocalizationDraft,
    subtitles: list[VideoLocalizationDubSubtitleCue],
    *,
    source_revision: str,
    eligible_clip_ids: set[str] | None = None,
) -> list[VideoLocalizationDubSubtitleCue]:
    """Recover only CQC-proven audible clips omitted by the full-track ASR."""

    output = list(subtitles)
    covered_clip_ids = {
        clip_id
        for subtitle in output
        for clip_id in subtitle.source_clip_ids
    }
    clip_by_id = {
        str(clip.get("clip_id") or ""): clip
        for clip in draft.timeline_clips
        if str(clip.get("clip_id") or "")
    }
    candidate_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in draft.generated_candidates
        if str(candidate.get("candidate_id") or "")
    }
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}

    audible_clips = sorted(
        (
            clip
            for clip in draft.timeline_clips
            if clip.get("track_id") == "dub"
            and clip.get("status") == "ready"
            and clip.get("clip_id") not in covered_clip_ids
            and (
                eligible_clip_ids is None
                or str(clip.get("clip_id") or "")
                in eligible_clip_ids
            )
        ),
        key=lambda clip: (
            int(clip.get("start_ms") or 0),
            str(clip.get("clip_id") or ""),
        ),
    )
    for clip in audible_clips:
        clip_id = str(clip.get("clip_id") or "")
        candidate_id = str(clip.get("candidate_id") or "")
        candidate = candidate_by_id.get(candidate_id)
        if not clip_id or candidate is None:
            continue
        report = candidate.get("cqc_report")
        if not isinstance(report, dict):
            continue
        transcript = report.get("transcript")
        audio_evidence = report.get("audio_evidence")
        if not isinstance(transcript, dict) or not isinstance(
            audio_evidence, dict
        ):
            continue
        expected_tokens = int(transcript.get("expected_tokens") or 0)
        matched_tokens = int(transcript.get("matched_tokens") or 0)
        if (
            expected_tokens < 1
            or matched_tokens != expected_tokens
            or float(transcript.get("coverage_ratio") or 0.0) < 0.999
            or float(transcript.get("extra_ratio") or 0.0) > 0.0
            or transcript.get("missing_tokens")
            or transcript.get("extra_tokens")
            or transcript.get("reference_only_extra_tokens")
        ):
            continue

        text = subtitle_punctuation.normalize_display_subtitle_punctuation(
            str(clip.get("tts_target_text") or "")
        ).strip()
        clip_start_ms = int(clip.get("start_ms") or 0)
        clip_end_ms = int(clip.get("end_ms") or 0)
        source_start_ms = int(clip.get("source_start_ms") or 0)
        speech_start_ms = int(audio_evidence.get("speech_start_ms") or 0)
        speech_end_ms = int(audio_evidence.get("speech_end_ms") or 0)
        start_ms = clip_start_ms + max(
            0,
            speech_start_ms - source_start_ms,
        )
        end_ms = clip_start_ms + min(
            max(0, clip_end_ms - clip_start_ms),
            max(0, speech_end_ms - source_start_ms),
        )
        lane = max(0, int(clip.get("dub_lane") or 0))
        if not text or end_ms <= start_ms:
            continue

        unsafe_overlap = False
        adjusted: list[VideoLocalizationDubSubtitleCue] = []
        for existing in output:
            if (
                lane not in existing.dub_lanes
                or existing.end_ms <= start_ms
                or existing.start_ms >= end_ms
            ):
                adjusted.append(existing)
                continue
            source_ends = [
                int(clip_by_id[source_id].get("end_ms") or 0)
                for source_id in existing.source_clip_ids
                if source_id in clip_by_id
            ]
            can_trim_display_exit = (
                existing.start_ms < start_ms
                and "timing:display-exit-extended"
                in existing.quality_flags
                and source_ends
                and max(source_ends) <= start_ms
            )
            if not can_trim_display_exit:
                unsafe_overlap = True
                break
            adjusted.append(
                existing.model_copy(
                    update={
                        "end_ms": start_ms,
                        "quality_flags": list(
                            dict.fromkeys(
                                [
                                    *existing.quality_flags,
                                    "timing:display-exit-trimmed-for-cqc-omission",
                                ]
                            )
                        ),
                    }
                )
            )
        if unsafe_overlap:
            continue
        output = adjusted

        source_cue_ids = list(
            dict.fromkeys(
                [
                    *(
                        clip.get("source_cue_ids")
                        if isinstance(clip.get("source_cue_ids"), list)
                        else []
                    ),
                    *(
                        [str(clip.get("cue_id"))]
                        if clip.get("cue_id")
                        else []
                    ),
                ]
            )
        )
        speaker_ids = {
            cue_by_id[cue_id].speaker_id
            for cue_id in source_cue_ids
            if cue_id in cue_by_id and cue_by_id[cue_id].speaker_id
        }
        audio_sha256 = (
            output[0].source_audio_sha256
            if output
            else str(candidate.get("audio_sha256") or "")
        )
        if len(audio_sha256) != 64:
            continue
        output.append(
            VideoLocalizationDubSubtitleCue(
                subtitle_id=_timeline_subtitle_id(
                    [clip_id],
                    len(output) + 1,
                    start_ms,
                ),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=(
                    next(iter(speaker_ids))
                    if len(speaker_ids) == 1
                    else None
                ),
                source_clip_ids=[clip_id],
                dub_lanes=[lane],
                source_audio_sha256=audio_sha256,
                needs_review=True,
                quality_flags=[
                    "recovered_from_exact_candidate_cqc",
                    "timing:cqc-voice-activity",
                    "asr_omission_review_required",
                ],
            )
        )
        covered_clip_ids.add(clip_id)

    output.sort(
        key=lambda item: (
            item.start_ms,
            item.end_ms,
            item.dub_lanes[0] if item.dub_lanes else -1,
            item.subtitle_id,
        )
    )
    return output


def apply_reviewed_subtitles(
    draft: VideoLocalizationDraft,
    *,
    source_revision: str,
    cues: list[dict[str, Any]],
) -> VideoLocalizationDraft:
    """Apply a complete, provenance-preserving review of the derived track."""

    if (
        not draft.dub_subtitle_source_revision
        or source_revision != draft.dub_subtitle_source_revision
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_STALE",
            "合成配音字幕的来源已经变化，请重新读取当前字幕后再提交复审结果。",
        )
    current = list(draft.dub_subtitles)
    if not current or not cues:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INCOMPLETE",
            "复审结果必须完整覆盖当前合成配音字幕。",
        )
    current_by_id = {item.subtitle_id: item for item in current}
    current_ids = [item.subtitle_id for item in current]
    current_index = {
        subtitle_id: index
        for index, subtitle_id in enumerate(current_ids)
    }
    consumed_ids: list[str] = []
    reviewed: list[VideoLocalizationDubSubtitleCue] = []
    video_duration_ms = int(draft.source_media.duration_ms or 0)

    for payload in cues:
        subtitle_id = str(payload.get("subtitle_id") or "").strip()
        source_ids = [
            str(value).strip()
            for value in payload.get("source_subtitle_ids", [])
            if str(value).strip()
        ]
        text = str(payload.get("text") or "").strip()
        try:
            start_ms = int(payload.get("start_ms"))
            end_ms = int(payload.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INVALID",
                "复审字幕必须提供有效的开始和结束时间。",
            ) from exc
        if (
            not subtitle_id
            or not source_ids
            or not text
            or len(source_ids) != len(set(source_ids))
            or subtitle_id != source_ids[0]
            or any(value not in current_by_id for value in source_ids)
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INVALID",
                "复审字幕必须引用有效、唯一且以保留字幕开头的来源字幕。",
            )
        source_indices = [current_index[value] for value in source_ids]
        if source_indices != list(
            range(source_indices[0], source_indices[0] + len(source_indices))
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INVALID",
                "只能合并时间线上连续的合成配音字幕。",
            )
        if (
            start_ms < 0
            or end_ms <= start_ms
            or (video_duration_ms and end_ms > video_duration_ms)
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INVALID",
                "复审字幕时间必须为视频范围内的正时长区间。",
            )
        sources = [current_by_id[value] for value in source_ids]
        speakers = {item.speaker_id for item in sources}
        source_hashes = {item.source_audio_sha256 for item in sources}
        if len(speakers) != 1 or len(source_hashes) != 1:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INVALID",
                "不能把不同说话人或不同配音来源的字幕合并。",
            )
        consumed_ids.extend(source_ids)
        quality_flags = list(
            dict.fromkeys(
                [
                    flag
                    for item in sources
                    for flag in item.quality_flags
                ]
                + ["readability:manual-review"]
            )
        )
        source_is_stale = SOURCE_CHANGED_QUALITY_FLAG in quality_flags
        reviewed.append(
            VideoLocalizationDubSubtitleCue(
                subtitle_id=subtitle_id,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=sources[0].speaker_id,
                source_clip_ids=list(
                    dict.fromkeys(
                        clip_id
                        for item in sources
                        for clip_id in item.source_clip_ids
                    )
                ),
                dub_lanes=sorted(
                    {
                        lane
                        for item in sources
                        for lane in item.dub_lanes
                    }
                ),
                source_audio_sha256=sources[0].source_audio_sha256,
                needs_review=source_is_stale,
                quality_flags=quality_flags,
            )
        )

    if consumed_ids != current_ids:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INCOMPLETE",
            "复审结果必须按原顺序且恰好一次覆盖全部当前字幕。",
        )
    for index, cue in enumerate(reviewed):
        for previous in reviewed[:index]:
            if (
                set(previous.dub_lanes).intersection(cue.dub_lanes)
                and previous.end_ms > cue.start_ms
                and cue.end_ms > previous.start_ms
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_OVERLAP",
                    "同一配音轨上的复审字幕时间不能重叠。",
                )
    return draft.model_copy(update={"dub_subtitles": reviewed})


def normalize_display_number_forms(
    draft: VideoLocalizationDraft,
    *,
    source_revision: str,
) -> tuple[VideoLocalizationDraft, int]:
    """Apply the current advisory screen-number policy to one derived track."""

    if (
        not draft.dub_subtitle_source_revision
        or source_revision != draft.dub_subtitle_source_revision
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_STALE",
            "合成配音字幕的来源已经变化，请重新读取当前字幕后再检查数字写法。",
        )
    changed_count = 0
    normalized: list[VideoLocalizationDubSubtitleCue] = []
    for cue in draft.dub_subtitles:
        number_text = (
            subtitle_punctuation.normalize_display_subtitle_numbers(
                cue.text
            )
        )
        if number_text == cue.text:
            normalized.append(cue)
            continue
        text = subtitle_punctuation.normalize_display_subtitle_punctuation(
            number_text
        ).strip()
        changed_count += 1
        normalized.append(
            cue.model_copy(
                update={
                    "text": text,
                    "quality_flags": list(
                        dict.fromkeys(
                            [
                                *cue.quality_flags,
                                "display:number-form-normalized",
                            ]
                        )
                    ),
                }
            )
        )
    if not changed_count:
        return draft, 0
    return draft.model_copy(update={"dub_subtitles": normalized}), changed_count


def reconcile_source_lifecycle(
    previous: VideoLocalizationDraft,
    current: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Reconcile one persisted derived track with its current audible source.

    A source-only edit keeps the last successful result visible but marks it
    stale. A command-owned replacement is accepted as-is. Starting a new
    workflow never mutates the derived track; successful commit is the sole
    replacement boundary.
    """

    if (
        not previous.dub_subtitles
        and previous.dub_subtitle_source_revision is None
    ):
        return current
    before = _source_structure_payload(previous)
    after = _source_structure_payload(current)
    if before == after:
        return current
    if (
        current.dub_subtitles != previous.dub_subtitles
        or current.dub_subtitle_source_revision
        != previous.dub_subtitle_source_revision
    ):
        # A command intentionally replaced the derived result in the same
        # save.  Do not rewrite the newly committed track as stale.
        return current
    before_clips = {item["clip_id"]: item for item in before["clips"]}
    after_clips = {item["clip_id"]: item for item in after["clips"]}
    changed_ids = {
        clip_id for clip_id in before_clips.keys() | after_clips.keys()
        if before_clips.get(clip_id) != after_clips.get(clip_id)
    }
    changed_lanes = {
        lane for lane in before["lane_mutes"].keys() | after["lane_mutes"].keys()
        if before["lane_mutes"].get(lane) != after["lane_mutes"].get(lane)
    }
    affected = [
        item for item in [*before["clips"], *after["clips"]]
        if item["clip_id"] in changed_ids or str(item["dub_lane"]) in changed_lanes
    ]
    affected_ids = {item["clip_id"] for item in affected}
    affected_ranges = [
        tuple(item["timeline_range"]) for item in affected
    ]
    invalidated = invalidate_source_result(
        current,
        affected_clip_ids=affected_ids,
        affected_ranges=affected_ranges,
    )
    return invalidated.model_copy(
        update={
            "dub_subtitle_dirty_scope": _accumulate_dirty_scope(
                invalidated,
                affected_clip_ids=affected_ids,
                affected_ranges=affected_ranges,
            )
        }
    )


def invalidate_source_result(
    current: VideoLocalizationDraft,
    *,
    affected_clip_ids: set[str] | None = None,
    affected_ranges: list[tuple[int, int]] | None = None,
) -> VideoLocalizationDraft:
    """Mark existing derived captions stale after an explicit source edit.

    Editorial commands already know which input they changed and need not
    resolve every audio source merely to discover that their edit happened.
    """
    if not current.dub_subtitles:
        return current
    return current.model_copy(
        update={
            "dub_subtitles": [
                cue.model_copy(
                    update={
                        "needs_review": True,
                        "quality_flags": list(
                            dict.fromkeys(
                                [
                                    *cue.quality_flags,
                                    SOURCE_CHANGED_QUALITY_FLAG,
                                ]
                            )
                        ),
                    }
                )
                if (
                    affected_clip_ids is None
                    or bool(set(cue.source_clip_ids) & affected_clip_ids)
                    or any(
                        cue.start_ms < end and cue.end_ms > start
                        for start, end in (affected_ranges or [])
                    )
                ) else cue
                for cue in current.dub_subtitles
            ],
        }
    )


def _accumulate_dirty_scope(
    draft: VideoLocalizationDraft,
    *,
    affected_clip_ids: set[str],
    affected_ranges: list[tuple[int, int]],
) -> VideoLocalizationDubSubtitleDirtyScope:
    """Keep the original cue identities while later source edits accumulate."""

    previous = draft.dub_subtitle_dirty_scope
    # A displayed cue may join several short/silent boundaries.  Follow those
    # joins to a fixed point: A(clip-1, clip-2) can touch B(clip-2, clip-3),
    # and selecting only one hop would still permit B to be replaced without
    # hearing clip-3.
    clip_ids = set(affected_clip_ids)
    clip_ids.update(previous.affected_clip_ids if previous else [])
    ranges = {
        (item.start_ms, item.end_ms)
        for item in (previous.affected_ranges if previous else [])
    }
    ranges.update(
        (int(start_ms), int(end_ms))
        for start_ms, end_ms in affected_ranges
        if end_ms > start_ms
    )
    while True:
        added_clip_ids: set[str] = set()
        added_ranges: set[tuple[int, int]] = set()
        for cue in draft.dub_subtitles:
            if _cue_in_regeneration_scope(
                cue,
                affected_clip_ids=clip_ids,
                affected_ranges=list(ranges),
            ):
                added_clip_ids.update(cue.source_clip_ids)
                added_ranges.add((cue.start_ms, cue.end_ms))
        new_clip_ids = added_clip_ids - clip_ids
        new_ranges = added_ranges - ranges
        if not new_clip_ids and not new_ranges:
            break
        clip_ids.update(new_clip_ids)
        ranges.update(new_ranges)
    fingerprints = dict(
        previous.replaceable_subtitle_fingerprints if previous else {}
    )
    for cue in draft.dub_subtitles:
        if _cue_in_regeneration_scope(
            cue,
            affected_clip_ids=clip_ids,
            affected_ranges=list(ranges),
        ):
            fingerprints.setdefault(
                cue.subtitle_id,
                _caption_fingerprint(cue),
            )
    return VideoLocalizationDubSubtitleDirtyScope(
        affected_clip_ids=sorted(clip_ids),
        affected_ranges=[
            VideoLocalizationDubSubtitleDirtyRange(
                start_ms=start_ms,
                end_ms=end_ms,
            )
            for start_ms, end_ms in sorted(ranges)
        ],
        replaceable_subtitle_fingerprints=fingerprints,
    )


def _cue_in_regeneration_scope(
    cue: VideoLocalizationDubSubtitleCue,
    *,
    affected_clip_ids: set[str],
    affected_ranges: list[tuple[int, int]],
) -> bool:
    return bool(set(cue.source_clip_ids) & affected_clip_ids) or any(
        cue.start_ms < end_ms and cue.end_ms > start_ms
        for start_ms, end_ms in affected_ranges
    )


def _caption_fingerprint(cue: VideoLocalizationDubSubtitleCue) -> str:
    encoded = json.dumps(
        cue.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_structure_revision(
    draft: VideoLocalizationDraft,
) -> str:
    """Cheap internal revision used to invalidate results on later edits."""

    return hashlib.sha256(
        json.dumps(
            _source_structure_payload(draft),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _source_structure_payload(draft: VideoLocalizationDraft) -> dict:
    subtitle_by_id = {
        item.subtitle_id: item
        for item in draft.localized_subtitles
    }
    cue_by_id = {item.cue_id: item for item in draft.cues}
    resolved_paths = timeline_audio_sources.resolve_dub_clip_audio_paths(
        draft
    )
    clips: list[dict] = []
    occupied_lanes: set[int] = set()
    for raw in draft.timeline_clips:
        clip = dict(raw)
        if clip.get("track_id") != "dub":
            continue
        clip_id = str(clip.get("clip_id") or "")
        resolved_path = resolved_paths.get(clip_id)
        if resolved_path is None:
            continue
        audio_path = str(resolved_path)
        lane = max(0, int(clip.get("dub_lane") or 0))
        occupied_lanes.add(lane)
        target_ids = [
            str(value)
            for value in (
                clip.get("target_subtitle_ids")
                or (
                    [clip.get("subtitle_id")]
                    if clip.get("subtitle_id")
                    else []
                )
            )
            if str(value or "").strip()
        ]
        references = [
            subtitle_by_id[item]
            for item in target_ids
            if item in subtitle_by_id
        ]
        start_ms = _clip_ms(clip, "start_ms", 0)
        end_ms = _clip_ms(clip, "end_ms", start_ms)
        source_ids = [
            str(value)
            for value in clip.get("source_cue_ids") or []
            if str(value or "").strip()
        ]
        clips.append(
            {
                "clip_id": str(clip.get("clip_id") or ""),
                "dub_lane": lane,
                "audio_identity": [
                    audio_path,
                    str(clip.get("result_id") or ""),
                    str(clip.get("generation_id") or ""),
                    str(clip.get("candidate_id") or ""),
                ],
                "timeline_range": [start_ms, end_ms],
                "source_range": [
                    _clip_ms(clip, "source_start_ms", 0),
                    _clip_ms(clip, "source_end_ms", 0),
                ],
                "target_subtitle_ids": target_ids,
                "source_cue_ids": source_ids,
                "reference_text": "".join(
                    (item.tts_text or item.text).strip()
                    for item in references
                ),
                "speaker_ids": sorted(
                    {
                        cue_by_id[item].speaker_id
                        for item in source_ids
                        if item in cue_by_id
                        and cue_by_id[item].speaker_id
                    }
                ),
            }
        )
    return {
        "lane_mutes": {
            str(lane): _lane_muted(draft, lane)
            for lane in sorted(occupied_lanes)
        },
        "clips": sorted(
            clips,
            key=lambda item: (
                item["timeline_range"][0],
                item["dub_lane"],
                item["clip_id"],
            ),
        ),
    }


def _audible_dub_clips(
    draft: VideoLocalizationDraft,
    *,
    allow_empty: bool = False,
) -> list[dict]:
    timeline_dub_clips = [
        dict(item)
        for item in draft.timeline_clips
        if dict(item).get("track_id") == "dub"
        and str(dict(item).get("status") or "").strip().lower()
        != "queued"
    ]
    if not timeline_dub_clips and not allow_empty:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_TRACK_MISSING",
            (
                "时间线上没有可识别的合成配音音频，"
                "请先完成至少一个配音片段的生成。"
            ),
        )
    resolved_paths = timeline_audio_sources.resolve_dub_clip_audio_paths(
        draft,
        timeline_dub_clips,
    )
    occupied_lanes = {
        max(0, int(item.get("dub_lane") or 0))
        for item in timeline_dub_clips
    }
    audible_lanes = {
        lane
        for lane in occupied_lanes
        if not _lane_muted(draft, lane)
    }
    if not audible_lanes and allow_empty:
        return []
    if not audible_lanes:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_LANES_ALL_MUTED",
            (
                "所有有内容的合成配音轨道都已静音。"
                "请至少打开一条合成配音轨道后再识别。"
            ),
        )
    selected_candidates = [
        item
        for item in timeline_dub_clips
        if max(0, int(item.get("dub_lane") or 0))
        in audible_lanes
    ]
    missing_clip_ids = [
        str(clip.get("clip_id") or f"dub-clip-{index}")
        for index, clip in enumerate(selected_candidates, start=1)
        if str(clip.get("clip_id") or "") not in resolved_paths
    ]
    if missing_clip_ids:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_MISSING",
            (
                f"有 {len(missing_clip_ids)} 个合成配音片段找不到音频，"
                "已停止生成字幕。"
            ),
            {"clip_ids": missing_clip_ids},
        )
    selected = [
        {
            **clip,
            "audio_path": str(
                resolved_paths[str(clip.get("clip_id") or "")]
            ),
        }
        for clip in selected_candidates
    ]
    probed_paths: set[Path] = set()
    for index, clip in enumerate(selected, start=1):
        clip_id = str(clip.get("clip_id") or "").strip()
        if not clip_id:
            clip_id = f"dub-clip-{index}"
            clip["clip_id"] = clip_id
        value = str(clip["audio_path"]).strip()
        path = Path(value)
        if not path.is_file():
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_NOT_FOUND",
                f"合成配音片段 {clip_id} 的音频文件不存在。",
            )
        if path not in probed_paths:
            try:
                audio_tools.probe_audio(path)
            except Exception as exc:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_INVALID",
                    f"合成配音片段 {clip_id} 的音频无法读取。",
                ) from exc
            probed_paths.add(path)
    return sorted(
        selected,
        key=lambda item: (
            _clip_ms(item, "start_ms", 0),
            max(0, int(item.get("dub_lane") or 0)),
            str(item.get("clip_id") or ""),
        ),
    )


def _lane_muted(
    draft: VideoLocalizationDraft,
    lane: int,
) -> bool:
    raw_states = draft.ui_state.get("dub_lane_states")
    raw_state = (
        raw_states.get(str(lane))
        if isinstance(raw_states, dict)
        else None
    )
    if isinstance(raw_state, dict):
        return raw_state.get("muted") is True
    if lane == 0:
        track_states = draft.ui_state.get("track_states")
        dub_state = (
            track_states.get("dub")
            if isinstance(track_states, dict)
            else None
        )
        if isinstance(dub_state, dict):
            return dub_state.get("muted") is True
    return False


def _source_revision(
    draft: VideoLocalizationDraft,
    clips: list[step_contracts.DubSubtitleSourceClip],
    references: list[step_contracts.DubSubtitleTextReference],
    *,
    video_frame_rate: float | None,
) -> str:
    occupied_lanes = sorted(
        {
            max(0, int(dict(item).get("dub_lane") or 0))
            for item in draft.timeline_clips
            if dict(item).get("track_id") == "dub"
        }
    )
    payload = {
        "occupied_lane_mutes": {
            str(lane): _lane_muted(draft, lane)
            for lane in occupied_lanes
        },
        "clips": [
            item.model_dump(mode="json")
            for item in clips
        ],
        "references": [
            item.model_dump(mode="json")
            for item in references
        ],
        "video_frame_rate": video_frame_rate,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _RenderedTimelineTrack:
    def __init__(
        self,
        clips: list[_DubSubtitleClip],
        audio_paths: Mapping[str, Path],
        *,
        timeline_duration_ms: int | None = None,
        output_path: Path | None = None,
        remove_on_exit: bool = True,
    ) -> None:
        self.clips = clips
        self.audio_paths = audio_paths
        self.path: Path | None = None
        self.audio_sha256 = ""
        self.output_path = output_path
        self.remove_on_exit = remove_on_exit
        requested_duration_ms = int(timeline_duration_ms or 0)
        last_clip_end_ms = max(
            item.timeline_end_ms for item in clips
        )
        if (
            requested_duration_ms > 0
            and last_clip_end_ms > requested_duration_ms
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_OUTSIDE_VIDEO",
                "合成配音片段超出视频时长，无法生成等长完整音频。",
            )
        self.duration_ms = (
            requested_duration_ms
            if requested_duration_ms > 0
            else last_clip_end_ms
        )

    def __enter__(self) -> _RenderedTimelineTrack:
        if self.output_path is None:
            handle = tempfile.NamedTemporaryFile(
                prefix="video-localization-dub-track-",
                suffix=".wav",
                delete=False,
            )
            handle.close()
            self.path = Path(handle.name)
        else:
            self.output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            self.path = self.output_path
            self.path.unlink(missing_ok=True)
        try:
            rendered = (
                timeline_audio_renderer.render_timeline_audio(
                    timeline_audio_renderer.TimelineAudioRenderInput(
                        timeline_duration_ms=self.duration_ms,
                        channel_mode="mono",
                        items=[
                            timeline_audio_renderer
                            .TimelineAudioRenderItem.from_editorial_ranges(
                                item_id=clip.clip_id,
                                source_id=clip.clip_id,
                                track_id="dub",
                                timeline_start_ms=(
                                    clip.timeline_start_ms
                                ),
                                timeline_end_ms=clip.timeline_end_ms,
                                source_start_ms=clip.source_start_ms,
                                source_end_ms=clip.source_end_ms,
                            )
                            for clip in self.clips
                        ],
                    ),
                    source_paths=self.audio_paths,
                    output_path=self.path,
                )
            )
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        self.audio_sha256 = rendered.audio_sha256
        return self

    def __exit__(self, *_args: object) -> None:
        if self.path is not None and self.remove_on_exit:
            self.path.unlink(missing_ok=True)


def _render_timeline_track(
    clips: list[_DubSubtitleClip],
    audio_paths: Mapping[str, Path],
    *,
    timeline_duration_ms: int | None = None,
    output_path: Path | None = None,
    remove_on_exit: bool = True,
) -> _RenderedTimelineTrack:
    return _RenderedTimelineTrack(
        clips,
        audio_paths,
        timeline_duration_ms=timeline_duration_ms,
        output_path=output_path,
        remove_on_exit=remove_on_exit,
    )


def _localized_display_text(value: str) -> str:
    return subtitle_punctuation.normalize_display_subtitle_punctuation(
        value
    ).strip()


def _asr_reference_corrections(
    asr_text: str,
    reference_text: str,
    *,
    limit: int = 200,
) -> list[_DubSubtitleCorrection]:
    """Return compact correction examples without exposing an unbounded transcript."""

    left_units = _content_units(asr_text)
    right_units = _content_units(reference_text)
    left_folded = "".join(item[0] for item in left_units)
    right_folded = "".join(item[0] for item in right_units)
    left_original = "".join(
        asr_text[item[1]] for item in left_units
    )
    right_original = "".join(
        reference_text[item[1]] for item in right_units
    )
    if (
        not left_folded
        or not right_folded
        or left_folded == right_folded
    ):
        return []
    opcodes = SequenceMatcher(
        None,
        left_folded,
        right_folded,
        autojunk=False,
    ).get_opcodes()
    corrections: list[_DubSubtitleCorrection] = []
    index = 0
    while index < len(opcodes) and len(corrections) < limit:
        tag, left_start, left_end, right_start, right_end = (
            opcodes[index]
        )
        if tag == "equal":
            index += 1
            continue
        block_left_start = left_start
        block_left_end = left_end
        block_right_start = right_start
        block_right_end = right_end
        index += 1
        while index + 1 < len(opcodes):
            equal = opcodes[index]
            changed = opcodes[index + 1]
            if (
                equal[0] != "equal"
                or changed[0] == "equal"
                or equal[2] - equal[1] > 1
            ):
                break
            block_left_end = changed[2]
            block_right_end = changed[4]
            index += 2
        before = left_original[block_left_start:block_left_end]
        after = right_original[block_right_start:block_right_end]
        kind: Literal["added", "removed", "changed"]
        if not before:
            kind = "added"
        elif not after:
            kind = "removed"
        else:
            kind = "changed"
        corrections.append(
            _DubSubtitleCorrection(
                before=before,
                after=after,
                kind=kind,
            )
        )
    return corrections


def _content_units(value: str) -> list[tuple[str, int]]:
    return [
        (character.casefold(), index)
        for index, character in enumerate(value)
        if character.isalnum()
    ]


def _normalized_content_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum()
    )


def _cues_from_asr_segments_exact(
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[VideoLocalizationCue]:
    """Keep one text cue per ASR compute window; this is not subtitle time."""

    return [
        VideoLocalizationCue(
            cue_id=f"dub-asr-{index:04d}",
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            en_subtitle_text=str(
                segment.raw_text or segment.corrected_text or ""
            ).strip(),
            source_duration_ms=segment.end_ms - segment.start_ms,
            source_text_raw=str(
                segment.raw_text or segment.corrected_text or ""
            ).strip(),
            timing_confidence="low",
            quality_flags=[
                "generated_by_dub_asr",
                "timing:audio-chunk-window",
            ],
        )
        for index, segment in enumerate(segments, start=1)
        if (
            segment.end_ms > segment.start_ms
            and str(
                segment.raw_text or segment.corrected_text or ""
            ).strip()
        )
    ]


def _cues_from_strict_alignment(
    proofread_cues: list[step_contracts.DubSubtitleSegmentTextChunk],
    words: list[VideoLocalizationAlignedWord],
    *,
    clips: list[step_contracts.DubSubtitleTimelineClip] | None = None,
    subtitle_entry_by_word_id: Mapping[str, int] | None = None,
) -> list[VideoLocalizationCue]:
    """Group real aligned words without inventing text or time."""

    words_by_segment: dict[
        str,
        list[VideoLocalizationAlignedWord],
    ] = {}
    for word in words:
        if word.timing_source != "forced_aligner":
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                "字幕时间中存在非声学对齐来源，因此没有保存结果。",
            )
        words_by_segment.setdefault(
            word.segment_id,
            [],
        ).append(word)

    output: list[VideoLocalizationCue] = []
    previous_end_ms = 0
    for proofread_cue in proofread_cues:
        aligned = words_by_segment.get(
            proofread_cue.cue_id,
            [],
        )
        sentence_units = _semantic_sentence_units(
            proofread_cue.text
        )
        expected_count = sum(
            len(transcription.display_tokens(unit))
            for unit in sentence_units
        )
        if (
            not aligned
            or expected_count != len(aligned)
        ):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                (
                    f"听写音频块 {proofread_cue.cue_id} "
                    "没有完整对应到声学字词时间。"
                ),
            )
        cursor = 0
        semantic_parts: list[
            tuple[str, list[VideoLocalizationAlignedWord]]
        ] = []
        for sentence in sentence_units:
            token_count = len(
                transcription.display_tokens(sentence)
            )
            selected = aligned[cursor : cursor + token_count]
            cursor += token_count
            semantic_parts.append((sentence, selected))
        semantic_parts = _merge_short_semantic_parts(
            semantic_parts
        )
        semantic_parts = _split_semantic_parts_at_speaker_changes(
            semantic_parts,
            clips or [],
        )
        semantic_parts = _merge_acoustically_inseparable_parts(
            semantic_parts
        )
        for sentence_index, (
            sentence,
            selected,
        ) in enumerate(semantic_parts, start=1):
            aligned_start_ms = selected[0].start_ms
            detected_start_ms = int(
                (subtitle_entry_by_word_id or {}).get(
                    selected[0].word_id,
                    aligned_start_ms,
                )
            )
            start_ms = (
                detected_start_ms
                if (
                    aligned_start_ms <= detected_start_ms
                    <= selected[0].end_ms
                )
                else aligned_start_ms
            )
            end_ms = selected[-1].end_ms
            if (
                end_ms <= start_ms
                or start_ms < previous_end_ms
            ):
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                    (
                        f"听写音频块 {proofread_cue.cue_id} "
                        "返回了倒序或零时长的声学字幕时间。"
                    ),
                )
            display_text = (
                subtitle_punctuation
                .normalize_display_subtitle_punctuation(
                    sentence
                )
                .strip()
            )
            if not display_text:
                continue
            output.append(
                VideoLocalizationCue(
                    cue_id=(
                        f"{proofread_cue.cue_id}-"
                        f"{sentence_index:02d}"
                    ),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    en_subtitle_text=display_text,
                    source_duration_ms=end_ms - start_ms,
                    source_text_raw=sentence,
                    timing_confidence="high",
                    quality_flags=list(
                        dict.fromkeys(
                            [
                                "generated_by_dub_asr",
                                "timing:forced-aligner",
                                *(
                                    ["timing:acoustic-entry-refined"]
                                    if start_ms > aligned_start_ms
                                    else []
                                ),
                            ]
                        )
                    ),
                )
            )
            previous_end_ms = end_ms
        if cursor != len(aligned):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
                (
                    f"听写音频块 {proofread_cue.cue_id} "
                    "仍有未被字幕覆盖的声学字词。"
                ),
            )
    if not output:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
            "声学对齐完成后没有得到可显示字幕。",
        )
    return output


def _split_semantic_parts_at_speaker_changes(
    parts: list[
        tuple[str, list[VideoLocalizationAlignedWord]]
    ],
    clips: list[step_contracts.DubSubtitleTimelineClip],
) -> list[
    tuple[str, list[VideoLocalizationAlignedWord]]
]:
    """Never keep words from different known speakers in one subtitle."""

    if not clips:
        return parts
    output: list[
        tuple[str, list[VideoLocalizationAlignedWord]]
    ] = []
    for original_text, selected in parts:
        if not selected:
            continue
        runs: list[
            tuple[str | None, list[VideoLocalizationAlignedWord]]
        ] = []
        for word in selected:
            speaker_id = _speaker_for_aligned_word(word, clips)
            if runs and runs[-1][0] == speaker_id:
                runs[-1][1].append(word)
            else:
                runs.append((speaker_id, [word]))
        if len(runs) == 1:
            output.append((original_text, selected))
            continue
        output.extend(
            (_display_text_from_aligned_words(run_words), run_words)
            for _speaker_id, run_words in runs
        )
    return output


def _merge_acoustically_inseparable_parts(
    parts: list[
        tuple[str, list[VideoLocalizationAlignedWord]]
    ],
) -> list[
    tuple[str, list[VideoLocalizationAlignedWord]]
]:
    """Merge adjacent parts whose real word timings cannot be separated."""

    merged: list[
        tuple[str, list[VideoLocalizationAlignedWord]]
    ] = []
    for text, words in parts:
        if not text or not words:
            continue
        current_words = list(words)
        if (
            merged
            and (
                current_words[0].start_ms
                < merged[-1][1][-1].end_ms
                or current_words[-1].end_ms
                <= current_words[0].start_ms
            )
        ):
            previous_text, previous_words = merged[-1]
            merged[-1] = (
                previous_text + text,
                previous_words + current_words,
            )
            continue
        merged.append((text, current_words))
    return merged


def _speaker_for_aligned_word(
    word: VideoLocalizationAlignedWord,
    clips: list[step_contracts.DubSubtitleTimelineClip],
) -> str | None:
    effective_end_ms = max(word.start_ms + 1, word.end_ms)
    overlaps = [
        (
            max(
                0,
                min(effective_end_ms, clip.timeline_end_ms)
                - max(word.start_ms, clip.timeline_start_ms),
            ),
            clip,
        )
        for clip in clips
        if clip.timeline_end_ms > word.start_ms
        and clip.timeline_start_ms < effective_end_ms
    ]
    if not overlaps:
        nearest = min(
            clips,
            key=lambda clip: min(
                abs(clip.timeline_start_ms - word.start_ms),
                abs(word.start_ms - clip.timeline_end_ms),
            ),
            default=None,
        )
        if nearest is None:
            return None
        distance_ms = min(
            abs(nearest.timeline_start_ms - word.start_ms),
            abs(word.start_ms - nearest.timeline_end_ms),
        )
        return nearest.speaker_id if distance_ms <= 1_000 else None
    _duration, selected = max(
        overlaps,
        key=lambda item: (
            item[0],
            -abs(item[1].timeline_start_ms - word.start_ms),
        ),
    )
    return selected.speaker_id


def _display_text_from_aligned_words(
    words: list[VideoLocalizationAlignedWord],
) -> str:
    output = ""
    previous_token = ""
    for word in words:
        token = str(word.text or "")
        if (
            output
            and previous_token
            and _needs_display_token_space(previous_token, token)
        ):
            output += " "
        output += token
        previous_token = token
    return output.strip()


def _needs_display_token_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_tail = left[-1]
    right_head = right[0]
    if not left_tail.isalnum() or not right_head.isalnum():
        return False
    return left_tail.isascii() or right_head.isascii()


def _merge_short_semantic_parts(
    parts: list[
        tuple[str, list[VideoLocalizationAlignedWord]]
    ],
) -> list[
    tuple[str, list[VideoLocalizationAlignedWord]]
]:
    merged = [
        (text, list(words))
        for text, words in parts
        if text and words
    ]
    index = 0
    while index < len(merged):
        text, words = merged[index]
        duration_ms = words[-1].end_ms - words[0].start_ms
        if duration_ms >= MIN_SEMANTIC_CUE_DURATION_MS:
            index += 1
            continue

        prefers_next = text.rstrip().endswith(
            ("，", ",", "：", ":", "—", "–", "－", "―")
        )
        if (
            prefers_next
            and index + 1 < len(merged)
            and (
                merged[index + 1][1][0].start_ms
                - words[-1].end_ms
            )
            <= SHORT_CUE_MERGE_GAP_MS
        ):
            next_text, next_words = merged[index + 1]
            merged[index : index + 2] = [
                (text + next_text, words + next_words)
            ]
            continue

        if (
            index > 0
            and (
                words[0].start_ms
                - merged[index - 1][1][-1].end_ms
            )
            <= SHORT_CUE_MERGE_GAP_MS
        ):
            previous_text, previous_words = merged[index - 1]
            merged[index - 1 : index + 1] = [
                (
                    previous_text + text,
                    previous_words + words,
                )
            ]
            index -= 1
            continue

        if (
            index + 1 < len(merged)
            and (
                merged[index + 1][1][0].start_ms
                - words[-1].end_ms
            )
            <= SHORT_CUE_MERGE_GAP_MS
        ):
            next_text, next_words = merged[index + 1]
            merged[index : index + 2] = [
                (text + next_text, words + next_words)
            ]
            continue
        index += 1
    return merged


def _semantic_sentence_units(text: str) -> list[str]:
    """Keep only semantic sentence boundaries needed by this workflow."""

    source = text.strip()
    units: list[str] = []
    start = 0
    for match in re.finditer(
        r"(?:——|…+|[，,。！？!?；;：:—–－―])|\n+",
        source,
    ):
        end = match.start() if match.group(0).startswith("\n") else match.end()
        unit = source[start:end].strip()
        if unit:
            units.append(unit)
        start = match.end()
    tail = source[start:].strip()
    if tail:
        units.append(tail)
    return units or ([source] if source else [])


def _proofread_document_text(
    asr_text: str,
    localized_text: str,
) -> tuple[str, dict[str, int]]:
    """Correct heard text conservatively without inserting unspoken passages."""

    asr_units = _content_units(asr_text)
    localized_units = _content_units(localized_text)
    if not asr_units:
        return "", {
            "unmatched_asr_char_count": 0,
            "unmatched_localized_char_count": len(localized_units),
        }
    if not localized_units:
        return asr_text, {
            "unmatched_asr_char_count": len(asr_units),
            "unmatched_localized_char_count": 0,
        }

    corrected = list(asr_text)
    unmatched_asr = 0
    unmatched_localized = 0

    def replace_units(
        left_start: int,
        right_start: int,
        count: int,
    ) -> None:
        for offset in range(count):
            asr_index = asr_units[left_start + offset][1]
            localized_index = localized_units[
                right_start + offset
            ][1]
            corrected[asr_index] = localized_text[localized_index]

    matcher = SequenceMatcher(
        None,
        [item[0] for item in asr_units],
        [item[0] for item in localized_units],
        autojunk=False,
    )
    for tag, left_start, left_end, right_start, right_end in (
        matcher.get_opcodes()
    ):
        left_length = left_end - left_start
        right_length = right_end - right_start
        if tag == "equal":
            replace_units(left_start, right_start, left_length)
            continue
        if tag == "insert":
            unmatched_localized += right_length
            continue
        if tag == "delete":
            unmatched_asr += left_length
            continue
        if (
            0 < left_length <= 3
            and right_length > left_length
            and not _is_numeric_display_form_mismatch(
                asr_text,
                asr_units[left_start:left_end],
                localized_text,
                localized_units[right_start:right_end],
            )
        ):
            replace_units(left_start, right_start, left_length)
            unmatched_localized += (
                right_length - left_length
            )
            continue
        if (
            0 < left_length == right_length <= 6
        ):
            replace_units(left_start, right_start, left_length)
        else:
            unmatched_asr += left_length
            unmatched_localized += right_length
    corrected_text = _preserve_unconfirmed_ascii_rewrites(
        asr_text,
        localized_text,
        "".join(corrected),
    )
    corrected_text = _apply_bounded_reference_token_replacements(
        corrected_text,
        localized_text,
    )
    corrected_text = _normalize_reference_supported_alphanumeric_forms(
        corrected_text,
        localized_text,
    ).strip()
    return corrected_text, {
        "unmatched_asr_char_count": unmatched_asr,
        "unmatched_localized_char_count": unmatched_localized,
    }


def _is_numeric_display_form_mismatch(
    left_text: str,
    left_units: list[tuple[str, int]],
    right_text: str,
    right_units: list[tuple[str, int]],
) -> bool:
    left = "".join(left_text[index] for _value, index in left_units)
    right = "".join(
        right_text[index] for _value, index in right_units
    )
    chinese_number_characters = set(
        "零〇一二两三四五六七八九十百千万点"
    )
    return (
        any(character.isascii() and character.isdigit() for character in left)
        and any(character in chinese_number_characters for character in right)
    ) or (
        any(character in chinese_number_characters for character in left)
        and any(character.isascii() and character.isdigit() for character in right)
    )


def _canonicalize_reference_display_forms(
    text: str,
    references: list[step_contracts.DubSubtitleTextReference],
) -> str:
    output = text
    # A one-character numeric display form is safe only inside its complete
    # confirmed reference. Applying ``二 -> 2`` globally can corrupt unrelated
    # years and quantities elsewhere in the transcript.
    exact_reference_forms = {
        reference.text: str(reference.display_text or "").strip()
        for reference in references
        if str(reference.text or "").strip()
        and str(reference.display_text or "").strip()
        and reference.text != str(reference.display_text or "").strip()
        and _same_spoken_display_form(
            reference.text,
            str(reference.display_text or "").strip(),
        )
    }
    for source, target in sorted(
        exact_reference_forms.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        output = output.replace(
            source,
            re.sub(
                (
                    r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])"
                    r"|(?<=[\u3400-\u9fff])\s+(?=\d)"
                    r"|(?<=\d)\s+(?=[\u3400-\u9fff])"
                ),
                "",
                target,
            ),
        )

    candidates: dict[str, set[str]] = {}
    for reference in references:
        display_text = str(reference.display_text or "").strip()
        if not display_text or display_text == reference.text:
            continue
        source_tokens = list(
            _PROOFREAD_TOKEN_PATTERN.finditer(reference.text)
        )
        target_tokens = list(
            _PROOFREAD_TOKEN_PATTERN.finditer(display_text)
        )
        matcher = SequenceMatcher(
            None,
            [match.group(0).casefold() for match in source_tokens],
            [match.group(0).casefold() for match in target_tokens],
            autojunk=False,
        )
        for tag, left_start, left_end, right_start, right_end in (
            matcher.get_opcodes()
        ):
            if tag != "replace":
                continue
            source_matches = source_tokens[left_start:left_end]
            target_matches = target_tokens[right_start:right_end]
            if (
                not source_matches
                or not target_matches
                or len(source_matches) > MAX_DISPLAY_FORM_TOKENS
                or len(target_matches) > MAX_DISPLAY_FORM_TOKENS
                or not _has_reference_token_content(target_matches)
                or not any(
                    re.search(r"[A-Za-z0-9]", match.group(0))
                    or any(
                        character in _REFERENCE_SEMANTIC_SYMBOLS
                        for character in match.group(0)
                    )
                    for match in target_matches
                )
            ):
                continue
            source = reference.text[
                source_matches[0].start():source_matches[-1].end()
            ]
            target = display_text[
                target_matches[0].start():target_matches[-1].end()
            ]
            if (
                source == target
                or (
                    len(source) < 2
                    and not re.search(r"[A-Za-z0-9]", source)
                    and not re.search(r"\d", target)
                    and not any(
                        character in _REFERENCE_SEMANTIC_SYMBOLS
                        for character in target
                    )
                )
                or _is_unsafe_numeric_fragment_replacement(
                    source,
                    target,
                )
                or not _same_spoken_display_form(source, target)
            ):
                continue
            candidates.setdefault(source, set()).add(target)
            compact_source = re.sub(r"\s+", "", source)
            if len(compact_source) >= 2 and compact_source != source:
                candidates.setdefault(compact_source, set()).add(target)
    replacements = [
        (source, next(iter(targets)))
        for source, targets in candidates.items()
        if len(targets) == 1
    ]
    for source, target in sorted(
        replacements,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        output = output.replace(source, target)
    return output


def _is_unsafe_numeric_fragment_replacement(
    source: str,
    target: str,
) -> bool:
    """Reject global one-character rewrites such as 二 -> 2 or 十 -> 10."""

    compact_source = re.sub(r"\s+", "", source)
    return (
        bool(re.search(r"\d", target))
        and bool(compact_source)
        and set(compact_source) <= _CHINESE_NUMBER_CHARACTERS
        and len(compact_source) < 2
    )


def _same_spoken_display_form(source: str, target: str) -> bool:
    return bool(
        _spoken_display_keys(source)
        & _spoken_display_keys(target)
    )


def _spoken_display_keys(value: str) -> set[str]:
    return {
        _spoken_display_key(value, expand_units=False),
        _spoken_display_key(value, expand_units=True),
    } - {""}


def _spoken_display_key(
    value: str,
    *,
    expand_units: bool,
) -> str:
    normalized = re.sub(
        r"(?<=\d),(?=\d{3}(?:\D|$))",
        "",
        str(value or ""),
    )
    normalized = re.sub(
        r"(?i)°\s*c",
        "摄氏度",
        normalized,
    ).replace("℃", "摄氏度")
    if expand_units:
        for pattern, spoken in _DISPLAY_UNIT_SPOKEN_FORMS:
            normalized = pattern.sub(spoken, normalized)
    normalized = normalized.replace("/", "每")
    normalized = text_normalizer.normalize_tts_pronunciation(
        normalized
    )
    normalized = normalized.replace("千米", "公里")
    normalized = re.sub(
        r"摄氏([零〇一二两三四五六七八九十百千万亿点]+)度",
        r"\1摄氏度",
        normalized,
    )
    return re.sub(
        r"[^a-z0-9\u3400-\u9fff]+",
        "",
        normalized.casefold(),
    )


def _preserve_unconfirmed_ascii_rewrites(
    asr_text: str,
    localized_text: str,
    corrected_text: str,
) -> str:
    localized_tokens = {
        match.group(0).casefold()
        for match in re.finditer(
            r"[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*",
            localized_text,
        )
    }
    output = list(corrected_text)
    for match in re.finditer(
        r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*",
        corrected_text,
    ):
        start, end = match.span()
        if (
            corrected_text[start:end] == asr_text[start:end]
            or match.group(0).casefold() in localized_tokens
        ):
            continue
        output[start:end] = asr_text[start:end]
    return "".join(output)


def _apply_bounded_reference_token_replacements(
    text: str,
    localized_text: str,
) -> str:
    asr_tokens = list(_PROOFREAD_TOKEN_PATTERN.finditer(text))
    localized_tokens = list(
        _PROOFREAD_TOKEN_PATTERN.finditer(localized_text)
    )
    matcher = SequenceMatcher(
        None,
        [
            match.group(0).casefold()
            for match in asr_tokens
        ],
        [
            match.group(0).casefold()
            for match in localized_tokens
        ],
        autojunk=False,
    )
    replacements: list[tuple[int, int, str]] = []
    for tag, left_start, left_end, right_start, right_end in (
        matcher.get_opcodes()
    ):
        left_length = left_end - left_start
        right_length = right_end - right_start
        if (
            tag != "replace"
            or not left_length
            or not right_length
            or left_length > MAX_REFERENCE_CORRECTION_TOKENS
            or right_length > MAX_REFERENCE_CORRECTION_TOKENS
        ):
            continue
        source_matches = asr_tokens[left_start:left_end]
        target_matches = localized_tokens[right_start:right_end]
        if not (
            _has_reference_token_content(source_matches)
            and _has_reference_token_content(target_matches)
        ):
            continue
        source_start = source_matches[0].start()
        source_end = source_matches[-1].end()
        target_start = target_matches[0].start()
        target_end = target_matches[-1].end()
        replacements.append(
            (
                source_start,
                source_end,
                localized_text[target_start:target_end],
            )
        )
    output = text
    for start, end, replacement in reversed(replacements):
        output = output[:start] + replacement + output[end:]
    return output


def _has_reference_token_content(
    matches: list[re.Match[str]],
) -> bool:
    return any(
        re.search(r"[A-Za-z0-9]", match.group(0))
        or any(
            character in _REFERENCE_SEMANTIC_SYMBOLS
            for character in match.group(0)
        )
        for match in matches
    )


def _normalize_reference_supported_alphanumeric_forms(
    text: str,
    localized_text: str,
) -> str:
    reference_tokens = {
        match.group(0).casefold()
        for match in re.finditer(
            r"[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*",
            localized_text,
        )
    }
    digit_by_character = {
        "零": "0",
        "一": "1",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }

    def replace_number_letter(
        match: re.Match[str],
    ) -> str:
        candidate = (
            digit_by_character[match.group(1)]
            + match.group(2)
        )
        return (
            candidate
            if candidate.casefold() in reference_tokens
            else match.group(0)
        )

    normalized = re.sub(
        r"([零一二三四五六七八九])\s*([A-Za-z][A-Za-z0-9]*)",
        replace_number_letter,
        text,
    )

    def replace_decimal(
        match: re.Match[str],
    ) -> str:
        candidate = (
            digit_by_character[match.group(1)]
            + "."
            + digit_by_character[match.group(2)]
        )
        return (
            candidate
            if candidate.casefold() in reference_tokens
            else match.group(0)
        )

    normalized = re.sub(
        r"([零一二三四五六七八九])点([零一二三四五六七八九])",
        replace_decimal,
        normalized,
    )

    def join_confirmed_ascii_token(
        match: re.Match[str],
    ) -> str:
        candidate = re.sub(r"\s+", "", match.group(0))
        return (
            candidate
            if candidate.casefold() in reference_tokens
            else match.group(0)
        )

    normalized = re.sub(
        r"\b(?:[A-Za-z0-9]\s+)+[A-Za-z0-9]\b",
        join_confirmed_ascii_token,
        normalized,
    )
    return normalized


def _proofread_asr_cues(
    cues: list[VideoLocalizationCue],
    reference_text: str,
) -> list[VideoLocalizationCue]:
    """Attach corrected text to its original ASR audio chunk exactly."""

    ordered = sorted(
        (
            item
            for item in cues
            if _normalized_content_text(
                str(item.en_subtitle_text or "")
            )
        ),
        key=lambda item: (
            int(item.start_ms or 0),
            int(item.end_ms or 0),
            item.cue_id,
        ),
    )
    chunk_lengths = [
        len(_content_units(str(item.en_subtitle_text or "")))
        for item in ordered
    ]
    raw_text = "".join(
        str(item.en_subtitle_text or "")
        for item in ordered
    )
    raw_units = _content_units(raw_text)
    corrected_units = _content_units(reference_text)
    if not ordered or not corrected_units:
        return []
    if sum(chunk_lengths) != len(raw_units):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_PROOFREAD_FAILED",
            "原始听写音频块无法形成连续文字。",
        )
    owner_by_raw_unit: list[int] = []
    for chunk_index, chunk_length in enumerate(chunk_lengths):
        owner_by_raw_unit.extend(
            [chunk_index] * chunk_length
        )
    owner_by_corrected_unit: list[int | None] = [
        None
    ] * len(corrected_units)
    matcher = SequenceMatcher(
        None,
        [item[0] for item in raw_units],
        [item[0] for item in corrected_units],
        autojunk=False,
    )
    for tag, left_start, left_end, right_start, right_end in (
        matcher.get_opcodes()
    ):
        if tag == "equal":
            for left_index, right_index in zip(
                range(left_start, left_end),
                range(right_start, right_end),
                strict=True,
            ):
                owner_by_corrected_unit[right_index] = (
                    owner_by_raw_unit[left_index]
                )
            continue
        source_owners = set(
            owner_by_raw_unit[left_start:left_end]
        )
        if not source_owners:
            # An insertion exactly at an ASR chunk boundary belongs to the
            # following chunk: it precedes that chunk's first heard unit.
            # At document end there is no following owner, so retain the
            # final chunk. This keeps ownership monotonic without guessing
            # across both audio windows.
            owner_index = (
                left_start
                if left_start < len(owner_by_raw_unit)
                else left_start - 1
            )
            if 0 <= owner_index < len(owner_by_raw_unit):
                source_owners = {owner_by_raw_unit[owner_index]}
        if len(source_owners) != 1:
            first_owner = min(source_owners)
            last_owner = max(source_owners)
            merged_members = ordered[first_owner : last_owner + 1]
            merged_window_ms = (
                int(merged_members[-1].end_ms or 0)
                - int(merged_members[0].start_ms or 0)
            )
            if len(merged_members) > 3 or merged_window_ms > 120_000:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUB_SUBTITLE_PROOFREAD_FAILED",
                    "校对修改跨越的听写音频范围过大，无法安全归属。",
                )
            # ASR chunks are computation windows, not subtitle boundaries. A
            # spelling correction may legitimately span one arbitrary chunk
            # edge (for example a product name split in the middle). Merge the
            # small adjacent window and rerun exact ownership instead of
            # guessing which side owns the replacement.
            merged_raw_text = "".join(
                str(item.en_subtitle_text or "")
                for item in merged_members
            )
            merged_cue = merged_members[0].model_copy(
                update={
                    "cue_id": (
                        f"{merged_members[0].cue_id}__through__"
                        f"{merged_members[-1].cue_id}"
                    ),
                    "start_ms": merged_members[0].start_ms,
                    "end_ms": merged_members[-1].end_ms,
                    "en_subtitle_text": merged_raw_text,
                    "source_text_raw": merged_raw_text,
                }
            )
            return _proofread_asr_cues(
                [
                    *ordered[:first_owner],
                    merged_cue,
                    *ordered[last_owner + 1 :],
                ],
                reference_text,
            )
        owner = next(iter(source_owners))
        for right_index in range(right_start, right_end):
            owner_by_corrected_unit[right_index] = owner
    if (
        any(owner is None for owner in owner_by_corrected_unit)
        or owner_by_corrected_unit
        != sorted(owner_by_corrected_unit)
        or set(owner_by_corrected_unit) != set(range(len(ordered)))
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_PROOFREAD_FAILED",
            "校对结果无法连续对应回原始听写音频块。",
        )

    output: list[VideoLocalizationCue] = []
    for chunk_index, cue in enumerate(ordered):
        owned_indexes = [
            index
            for index, owner in enumerate(
                owner_by_corrected_unit
            )
            if owner == chunk_index
        ]
        first_unit_index = owned_indexes[0]
        next_chunk_unit_index = (
            owner_by_corrected_unit.index(chunk_index + 1)
            if chunk_index + 1 < len(ordered)
            else len(corrected_units)
        )
        start_offset = (
            0
            if chunk_index == 0
            else corrected_units[first_unit_index][1]
        )
        end_offset = (
            len(reference_text)
            if next_chunk_unit_index >= len(corrected_units)
            else corrected_units[next_chunk_unit_index][1]
        )
        corrected = reference_text[
            start_offset:end_offset
        ].strip()
        if not corrected:
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_PROOFREAD_FAILED",
                f"听写音频块 {cue.cue_id} 的校对文字为空。",
            )
        output.append(
            cue.model_copy(
                update={
                    "en_subtitle_text": corrected,
                    "source_text_raw": str(
                        cue.en_subtitle_text or ""
                    ).strip(),
                }
            )
        )
    return output


def _overlapping_clips(
    clips: list[step_contracts.DubSubtitleTimelineClip],
    start_ms: int,
    end_ms: int,
) -> list[step_contracts.DubSubtitleTimelineClip]:
    overlaps = [
        item
        for item in clips
        if item.timeline_end_ms > start_ms
        and item.timeline_start_ms < end_ms
    ]
    if overlaps:
        overlap_ms_by_id = {
            item.clip_id: max(
                0,
                min(end_ms, item.timeline_end_ms)
                - max(start_ms, item.timeline_start_ms),
            )
            for item in overlaps
        }
        meaningful = [
            item
            for item in overlaps
            if overlap_ms_by_id[item.clip_id] > 80
        ]
        if meaningful:
            return meaningful
        return overlaps
    nearest = min(
        clips,
        key=lambda item: min(
            abs(item.timeline_start_ms - end_ms),
            abs(start_ms - item.timeline_end_ms),
        ),
    )
    distance_ms = min(
        abs(nearest.timeline_start_ms - end_ms),
        abs(start_ms - nearest.timeline_end_ms),
    )
    return [nearest] if distance_ms <= 1_000 else []


def _timeline_subtitle_id(
    source_clip_ids: list[str],
    cue_index: int,
    start_ms: int,
) -> str:
    payload = (
        f"{','.join(source_clip_ids)}:{cue_index}:{start_ms}"
    )
    return "dub_" + hashlib.sha1(
        payload.encode("utf-8")
    ).hexdigest()[:16]


def _clip_ms(clip: dict, key: str, default: int) -> int:
    value = clip.get(key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_CLIP_RANGE_INVALID",
            (
                f"合成配音片段 {clip.get('clip_id') or ''} "
                f"的时间参数 {key} 无效。"
            ),
        ) from exc


def _ensure_active(
    is_cancelled: Callable[[], bool] | None,
) -> None:
    if is_cancelled is not None and is_cancelled():
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
            "合成配音字幕识别任务已取消。",
        )
