"""Deterministic source locking for the localization workflow.

This module is deliberately independent from task queues and persistence.  It
turns the current ASR draft into a path-free, versioned snapshot that later
localization tasks can consume without reading mutable project state.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import localization_timeline
from app.domains.video_localization.schemas import (
    AlignedWordTimingSource,
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
)
from app.domains.video_localization.timeline_timecode import format_timeline_range


_DOWNSTREAM_CUE_FLAG_PREFIXES = (
    "display_",
    "generated_localization",
    "localization",
    "localized_",
    "tts_",
    "zh_",
)
_DOWNSTREAM_CUE_FLAGS = {
    "linked_by_timing",
    "needs_zh_localization",
}

_SOURCE_OPERATIONAL_FLAG_PREFIXES = (
    "engine:", "segmentation:", "timing:", "boundary-analysis:", "punctuation:",
)
_SOURCE_OPERATIONAL_FLAGS = frozenset({"generated_by_asr", "llm_transcript_reviewed"})

SOURCE_CONFIDENCE_INTERPRETATION_POLICY = (
    "制作职责边界：识别可信度、画面未显示或外部事实未经核实，都是供制作判断的元数据，"
    "不代表作者在犹豫、否认或作事实核查。不得把这些判断写成旁白评论，也不得因此额外加入"
    "“可能”“存疑”“不能确定”等原文没有的措辞。对可理解的源义，保留作者原有断言、观点强度、"
    "条件和否定；真正无法确定的词，不猜出新事实，且不把制作诊断混入正文。"
)


def project_localization_source_quality_flags(flags: list[str]) -> list[str]:
    """Preserve meaning uncertainty, not acoustic/display processing provenance."""
    return [flag for flag in flags if flag not in _SOURCE_OPERATIONAL_FLAGS
            and not flag.startswith(_SOURCE_OPERATIONAL_FLAG_PREFIXES)]


class LocalizationSourceCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_id: str | None = None
    speaker_cluster_id: str | None = None
    source_word_ids: list[str] = Field(min_length=1)
    quality_flags: list[str] = Field(default_factory=list)


class LocalizationSourceWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    display_entry_ms: int | None = Field(default=None, ge=0)
    speaker_cluster_id: str | None = None
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False
    timing_confidence: Literal["high", "medium", "low"]
    timing_source: AlignedWordTimingSource


class LocalizationSourcePause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundary_id: str = Field(min_length=1)
    left_word_id: str = Field(min_length=1)
    right_word_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    gap_ms: int = Field(ge=0)
    low_energy_ms: int = Field(ge=0)
    low_energy_ratio: float = Field(ge=0, le=1)
    confidence: Literal["none", "low", "medium", "high"]
    analysis_version: str = Field(min_length=1)


class LocalizationSourceSpeaker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str = Field(min_length=1)
    display_name: str | None = None
    acoustic_cluster_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class LocalizationSourceGlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    glossary_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    corrected_source_text: str | None = None
    localized_text: str | None = None
    notes: str | None = None


class LocalizationSourceLockInput(BaseModel):
    """Serializable, path-free input consumed by the source-lock operation."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-source-lock-input-v1"] = "localization-source-lock-input-v1"
    asr_revision_id: str = Field(min_length=1)
    asr_source_track_id: str | None = None
    asr_source_audio_sha256: str | None = None
    language: str = Field(min_length=1)
    upstream_source_fingerprint: str = Field(min_length=64, max_length=64)
    cues: list[LocalizationSourceCue] = Field(min_length=1)
    words: list[LocalizationSourceWord] = Field(min_length=1)
    pauses: list[LocalizationSourcePause] = Field(default_factory=list)
    speakers: list[LocalizationSourceSpeaker] = Field(default_factory=list)
    glossary: list[LocalizationSourceGlossaryEntry] = Field(default_factory=list)
    scene_context: str = ""
    omitted_unsupported_cue_ids: list[str] = Field(default_factory=list)


class LocalizationSourceComponentFingerprints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cues: str = Field(min_length=64, max_length=64)
    words: str = Field(min_length=64, max_length=64)
    pauses: str = Field(min_length=64, max_length=64)
    speakers: str = Field(min_length=64, max_length=64)
    glossary: str = Field(min_length=64, max_length=64)
    scene_context: str = Field(min_length=64, max_length=64)


class LocalizationSourceLockWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "speaker_data_missing",
        "speaker_reference_missing",
        "audio_pauses_missing",
        "source_audio_fingerprint_missing",
        "unsupported_non_speech_run_omitted",
        "zero_width_word_timing",
    ]
    message: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class LocalizationSourceLockQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    cue_count: int = Field(ge=1)
    word_count: int = Field(ge=1)
    pause_count: int = Field(ge=0)
    speaker_count: int = Field(ge=0)
    glossary_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    cue_ids_unique: bool
    word_ids_unique: bool
    timing_ordered: bool
    word_references_complete: bool
    pause_references_complete: bool
    model_call_count: Literal[0] = 0


class LocalizationSourceLockResult(BaseModel):
    """Immutable source snapshot used by all later localization tasks."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-source-lock-v1"] = "localization-source-lock-v1"
    input: LocalizationSourceLockInput
    source_fingerprint: str = Field(min_length=64, max_length=64)
    component_fingerprints: LocalizationSourceComponentFingerprints
    warnings: list[LocalizationSourceLockWarning] = Field(default_factory=list)
    quality_summary: LocalizationSourceLockQualitySummary


def build_localization_source_input(
    draft: VideoLocalizationDraft,
) -> LocalizationSourceLockInput:
    """Build the safe, serializable input without copying local media paths."""

    from app.domains.video_localization.asr_uncertainty import project_asr_uncertainty

    transcription = draft.transcription
    if transcription is None or not transcription.words:
        raise ValueError("本土化需要完整的 ASR 逐词时间，请先完成听写与逐词对齐。")
    uncertainty = project_asr_uncertainty(transcription)

    all_source_cues = [cue for cue in draft.cues if str(cue.en_subtitle_text or "").strip()]
    if not all_source_cues:
        raise ValueError("本土化需要至少一条有文字的 ASR 字幕。")

    source_word_by_id = {word.word_id: word for word in transcription.words}
    projected_word_times = _project_supported_word_times(transcription.words)
    unresolved_extreme_words = [
        word
        for word in transcription.words
        if (
            word.end_ms - word.start_ms >= 3_000
            and word.timing_source == "forced_aligner"
            and word.speaker_confidence is not None
            and word.speaker_confidence < 0.2
            and projected_word_times[word.word_id]
            == (word.start_ms, word.end_ms)
        )
    ]
    if unresolved_extreme_words:
        raise ValueError(
            "ASR 中有超长逐词时间，但分离人声没有提供唯一连续的声学区间；"
            "请先从逐词对齐节点刷新 ASR，不能用猜测时间继续本土化。"
        )
    omitted_unsupported_cue_ids = _unsupported_non_speech_run_cue_ids(
        all_source_cues,
        word_by_id=source_word_by_id,
    )
    omitted_unsupported_cue_id_set = set(omitted_unsupported_cue_ids)
    source_cues = [cue for cue in all_source_cues if cue.cue_id not in omitted_unsupported_cue_id_set]
    if not source_cues:
        raise ValueError("本土化没有找到可确认的人声字幕。")

    cues: list[LocalizationSourceCue] = []
    for cue in source_cues:
        if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
            raise ValueError(f"ASR 字幕 {cue.cue_id} 缺少有效时间，不能锁定本土化输入。")
        if not cue.source_word_ids:
            raise ValueError(f"ASR 字幕 {cue.cue_id} 缺少逐词来源，不能锁定本土化输入。")
        cue_word_times = [
            projected_word_times[word_id] for word_id in cue.source_word_ids if word_id in projected_word_times
        ]
        projected_start_ms = max(
            cue.start_ms,
            cue_word_times[0][0] if cue_word_times else cue.start_ms,
        )
        projected_end_ms = min(
            cue.end_ms,
            cue_word_times[-1][1] if cue_word_times else cue.end_ms,
        )
        if projected_end_ms <= projected_start_ms:
            projected_start_ms = cue.start_ms
            projected_end_ms = cue.end_ms
        cues.append(
            LocalizationSourceCue(
                cue_id=cue.cue_id,
                start_ms=projected_start_ms,
                end_ms=projected_end_ms,
                text=str(cue.en_subtitle_text or "").strip(),
                speaker_id=cue.speaker_id,
                speaker_cluster_id=cue.speaker_cluster_id,
                source_word_ids=list(cue.source_word_ids),
                quality_flags=uncertainty.cue_flags(_source_quality_flags(cue.quality_flags), list(cue.source_word_ids)),
            )
        )

    retained_word_ids = {word_id for cue in source_cues for word_id in cue.source_word_ids}
    words = [
        LocalizationSourceWord(
            word_id=word.word_id,
            segment_id=word.segment_id,
            text=word.text.strip(),
            start_ms=projected_word_times[word.word_id][0],
            end_ms=projected_word_times[word.word_id][1],
            display_entry_ms=(
                max(
                    transcription.subtitle_entry_by_word_id[word.word_id],
                    projected_word_times[word.word_id][0],
                )
                if word.word_id in transcription.subtitle_entry_by_word_id
                else None
            ),
            speaker_cluster_id=word.speaker_cluster_id,
            speaker_confidence=word.speaker_confidence,
            has_speaker_overlap=word.has_speaker_overlap,
            timing_confidence=word.timing_confidence,
            timing_source=word.timing_source,
        )
        for word in transcription.words
        if word.word_id in retained_word_ids
    ]
    retained_word_id_set = {word.word_id for word in words}
    pauses = [
        LocalizationSourcePause(
            boundary_id=item.boundary_id,
            left_word_id=item.left_word_id,
            right_word_id=item.right_word_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            gap_ms=item.gap_ms,
            low_energy_ms=item.low_energy_ms,
            low_energy_ratio=item.low_energy_ratio,
            confidence=item.confidence,
            analysis_version=item.analysis_version,
        )
        for item in transcription.audio_boundary_features
        if item.left_word_id in retained_word_id_set and item.right_word_id in retained_word_id_set
    ]
    speakers = sorted(
        (
            LocalizationSourceSpeaker(
                speaker_id=speaker.speaker_id,
                display_name=speaker.display_name,
                acoustic_cluster_ids=list(speaker.acoustic_cluster_ids),
                notes=speaker.notes,
            )
            for speaker in draft.speakers
        ),
        key=lambda item: item.speaker_id,
    )
    glossary = sorted(
        (
            LocalizationSourceGlossaryEntry(
                glossary_id=item.glossary_id,
                source_text=item.source_text,
                corrected_source_text=item.corrected_source_text,
                localized_text=item.zh_text,
                notes=item.notes,
            )
            for item in draft.glossary
        ),
        key=lambda item: item.glossary_id,
    )
    return LocalizationSourceLockInput(
        asr_revision_id=transcription.revision_id,
        asr_source_track_id=transcription.alignment_source_track_id or transcription.source_track_id,
        asr_source_audio_sha256=transcription.alignment_audio_sha256 or transcription.source_audio_sha256,
        language=transcription.language,
        upstream_source_fingerprint=localization_timeline.source_fingerprint(draft),
        cues=cues,
        words=words,
        pauses=pauses,
        speakers=speakers,
        glossary=glossary,
        scene_context=draft.scene_context,
        omitted_unsupported_cue_ids=omitted_unsupported_cue_ids,
    )


def current_source_fingerprint(
    draft: VideoLocalizationDraft,
) -> str:
    """Fingerprint the current draft in the same contract used at commit."""

    request = DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
    return DEFAULT_LOCALIZATION_PIPELINE.lock_source(request).source_fingerprint


def _source_quality_flags(flags: list[str]) -> list[str]:
    """Keep only flags owned by the English source/ASR lifecycle."""

    return [
        flag
        for flag in flags
        if flag not in _DOWNSTREAM_CUE_FLAGS and not flag.startswith(_DOWNSTREAM_CUE_FLAG_PREFIXES)
    ]


class LocalizationPipeline:
    """Public facade for deterministic localization source preparation."""

    @staticmethod
    def build_input(
        draft: VideoLocalizationDraft,
    ) -> LocalizationSourceLockInput:
        return build_localization_source_input(draft)

    def lock_source(
        self,
        request: LocalizationSourceLockInput,
    ) -> LocalizationSourceLockResult:
        cue_ids = [item.cue_id for item in request.cues]
        word_ids = [item.word_id for item in request.words]
        pause_ids = [item.boundary_id for item in request.pauses]
        speaker_ids = [item.speaker_id for item in request.speakers]
        glossary_ids = [item.glossary_id for item in request.glossary]

        _require_unique(cue_ids, "ASR 字幕 ID")
        _require_unique(word_ids, "逐词时间 ID")
        _require_unique(pause_ids, "声音停顿 ID")
        _require_unique(speaker_ids, "说话人 ID")
        _require_unique(glossary_ids, "术语 ID")
        _require_ordered_timing(request.cues, "ASR 字幕")
        _require_ordered_timing(request.words, "逐词时间", allow_zero_width=True)
        _require_ordered_timing(request.pauses, "声音停顿")

        word_position = {word_id: index for index, word_id in enumerate(word_ids)}
        claimed_word_ids: list[str] = []
        for cue in request.cues:
            if len(set(cue.source_word_ids)) != len(cue.source_word_ids):
                raise ValueError(f"ASR 字幕 {cue.cue_id} 重复引用了同一个逐词时间。")
            unknown = [word_id for word_id in cue.source_word_ids if word_id not in word_position]
            if unknown:
                raise ValueError(f"ASR 字幕 {cue.cue_id} 引用了不存在的逐词时间：" + "、".join(unknown))
            positions = [word_position[word_id] for word_id in cue.source_word_ids]
            if positions != list(range(positions[0], positions[0] + len(positions))):
                raise ValueError(f"ASR 字幕 {cue.cue_id} 的逐词来源不连续。")
            claimed_word_ids.extend(cue.source_word_ids)
        if claimed_word_ids != word_ids:
            raise ValueError("ASR 字幕与逐词时间没有按同一顺序完整对应，不能锁定本土化输入。")

        for pause in request.pauses:
            if pause.left_word_id not in word_position or pause.right_word_id not in word_position:
                raise ValueError(f"声音停顿 {pause.boundary_id} 引用了不存在的逐词时间。")
            if word_position[pause.right_word_id] != word_position[pause.left_word_id] + 1:
                raise ValueError(f"声音停顿 {pause.boundary_id} 没有连接相邻词。")
            if pause.boundary_id != (f"{pause.left_word_id}:{pause.right_word_id}"):
                raise ValueError(f"声音停顿 {pause.boundary_id} 的来源 ID 不一致。")

        warnings: list[LocalizationSourceLockWarning] = []
        zero_width_word_ids = [
            word.word_id
            for word in request.words
            if word.start_ms == word.end_ms
        ]
        if zero_width_word_ids:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="zero_width_word_timing",
                    message=(
                        "部分逐词时间只有点锚，已保留原始证据；"
                        "它们不能视为可靠的声学时长。"
                    ),
                    source_ids=zero_width_word_ids,
                )
            )
        if request.omitted_unsupported_cue_ids:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="unsupported_non_speech_run_omitted",
                    message=(
                        "已忽略一段缺少说话人和可靠逐词对齐、且语速证据"
                        "不符合正常讲话的 ASR 文字；该时间范围不会生成"
                        "本土化字幕或配音。"
                    ),
                    source_ids=list(request.omitted_unsupported_cue_ids),
                )
            )
        known_speakers = set(speaker_ids)
        missing_speaker_refs = sorted(
            {cue.speaker_id for cue in request.cues if cue.speaker_id and cue.speaker_id not in known_speakers}
        )
        cues_without_speaker = [cue.cue_id for cue in request.cues if not cue.speaker_id]
        if not request.speakers or cues_without_speaker:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="speaker_data_missing",
                    message=("部分字幕还没有说话人资料；不影响继续生成中文，但后续人物口吻只能使用通用上下文。"),
                    source_ids=cues_without_speaker,
                )
            )
        if missing_speaker_refs:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="speaker_reference_missing",
                    message=("部分字幕引用的说话人还没有人物资料；已保留原始说话人 ID。"),
                    source_ids=missing_speaker_refs,
                )
            )
        if not request.pauses:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="audio_pauses_missing",
                    message=("当前没有声音停顿分析；不影响生成中文，后续断句会先使用标点和逐词时间。"),
                )
            )
        if not request.asr_source_audio_sha256:
            warnings.append(
                LocalizationSourceLockWarning(
                    code="source_audio_fingerprint_missing",
                    message=("旧 ASR 结果没有保存音频指纹；本次仍会用字幕、逐词时间和 ASR 修订号锁定输入。"),
                )
            )

        components = LocalizationSourceComponentFingerprints(
            cues=_fingerprint(request.cues),
            words=_fingerprint(request.words),
            pauses=_fingerprint(request.pauses),
            speakers=_fingerprint(request.speakers),
            glossary=_fingerprint(request.glossary),
            scene_context=_fingerprint(request.scene_context),
        )
        source_fingerprint = _fingerprint(
            {
                "contract_version": "localization-source-lock-v1",
                "asr_revision_id": request.asr_revision_id,
                "upstream_source_fingerprint": (request.upstream_source_fingerprint),
                "components": components,
            }
        )
        return LocalizationSourceLockResult(
            input=request,
            source_fingerprint=source_fingerprint,
            component_fingerprints=components,
            warnings=warnings,
            quality_summary=LocalizationSourceLockQualitySummary(
                status="warning" if warnings else "passed",
                cue_count=len(request.cues),
                word_count=len(request.words),
                pause_count=len(request.pauses),
                speaker_count=len(request.speakers),
                glossary_count=len(request.glossary),
                warning_count=len(warnings),
                cue_ids_unique=True,
                word_ids_unique=True,
                timing_ordered=True,
                word_references_complete=True,
                pause_references_complete=True,
            ),
        )


def project_localization_source_lock_step_result(
    result: LocalizationSourceLockResult,
    *,
    sample_limit: int = 8,
) -> dict:
    """Return deterministic Chinese reader/debug details for the task panel."""

    samples = _even_samples(result.input.cues, limit=sample_limit)
    sample_items = [
        {
            "title": cue.cue_id,
            "text": cue.text,
            "meta": format_timeline_range(cue.start_ms, cue.end_ms),
            "facts": [
                {
                    "label": "说话人",
                    "value": cue.speaker_id or "暂未区分",
                },
                {
                    "label": "逐词来源",
                    "value": (f"{cue.source_word_ids[0]} – {cue.source_word_ids[-1]}"),
                },
            ],
            "links": [],
            "tone": "neutral",
        }
        for cue in samples
    ]
    warning_items = [
        {
            "title": "输入提醒",
            "text": warning.message,
            "facts": (
                [
                    {
                        "label": "相关来源",
                        "value": "、".join(warning.source_ids),
                    }
                ]
                if warning.source_ids
                else []
            ),
            "links": [],
            "tone": "warning",
        }
        for warning in result.warnings
    ]
    return {
        "label": "固定本次英文源数据",
        "order": 10,
        "status": ("warning" if result.quality_summary.status == "warning" else "success"),
        "purpose": ("把最终 ASR 字幕、逐词时间和相关背景整理成一份不会随项目编辑变化的标准输入。"),
        "summary": (
            f"已锁定 {len(result.input.cues)} 条原文字幕和 "
            f"{len(result.input.words)} 个逐词时间；"
            f"{len(result.warnings)} 条提醒不会中断后续流程。"
            if result.warnings
            else (f"已锁定 {len(result.input.cues)} 条原文字幕和 {len(result.input.words)} 个逐词时间，来源关系完整。")
        ),
        "metrics": [
            {"label": "原文字幕", "value": str(len(result.input.cues))},
            {"label": "逐词时间", "value": str(len(result.input.words))},
            {"label": "声音停顿", "value": str(len(result.input.pauses))},
            {"label": "说话人", "value": str(len(result.input.speakers))},
            {"label": "术语", "value": str(len(result.input.glossary))},
        ],
        "sections": [
            {
                "title": "均匀抽查",
                "items": sample_items,
            },
            *([{"title": "输入提醒", "items": warning_items}] if warning_items else []),
        ],
        "review_targets": [
            {
                "title": "源输入提醒",
                "detail": warning.message,
            }
            for warning in result.warnings
        ],
        "coverage": {
            "mode": "sample",
            "shown_count": len(samples),
            "total_count": len(result.input.cues),
            "unit": "条原文字幕",
        },
        "debug": {
            "description": ("用于确认后续每一步读取的是同一份 ASR 结果，并检查各类来源是否完整。"),
            "metrics": [
                {
                    "label": "输出契约",
                    "value": result.contract_version,
                },
                {
                    "label": "输入契约",
                    "value": result.input.contract_version,
                },
                {
                    "label": "ASR 修订号",
                    "value": result.input.asr_revision_id,
                },
                {
                    "label": "源输入指纹",
                    "value": result.source_fingerprint,
                },
                {"label": "模型调用", "value": "0 次"},
                {"label": "输入 Token", "value": "0"},
                {"label": "输出 Token", "value": "0"},
                {"label": "模型费用", "value": "0"},
            ],
            "sections": [
                {
                    "title": "组件指纹",
                    "items": [
                        {
                            "title": label,
                            "text": value,
                            "facts": [],
                            "links": [],
                            "tone": "muted",
                        }
                        for label, value in (
                            ("原文字幕", result.component_fingerprints.cues),
                            ("逐词时间", result.component_fingerprints.words),
                            ("声音停顿", result.component_fingerprints.pauses),
                            ("说话人", result.component_fingerprints.speakers),
                            ("术语", result.component_fingerprints.glossary),
                            (
                                "场景说明",
                                result.component_fingerprints.scene_context,
                            ),
                        )
                    ],
                }
            ],
            "notes": [
                "这一步只整理和校验已有数据，不调用语言模型，也不修改原文字幕。",
                "输出只含业务字段和内容指纹，不含视频、音频或缓存的本地路径。",
            ],
        },
    }


DEFAULT_LOCALIZATION_PIPELINE = LocalizationPipeline()


def _project_supported_word_times(
    words: list[VideoLocalizationAlignedWord],
) -> dict[str, tuple[int, int]]:
    """Project only acoustically supported spans without mutating ASR evidence.

    A forced aligner can stretch one token across an action-only interval.
    Diarization records the exact contiguous interval where the assigned
    speaker actually overlaps that raw word.  Only an extreme, weakly
    supported forced-alignment span may use that factual interval downstream;
    the transcription record always keeps the original timing for diagnosis.
    """

    projected = {word.word_id: (word.start_ms, word.end_ms) for word in words}
    for word in words:
        duration_ms = word.end_ms - word.start_ms
        support_start_ms = word.acoustic_support_start_ms
        support_end_ms = word.acoustic_support_end_ms
        if not (
            duration_ms >= 3_000
            and word.timing_source == "forced_aligner"
            and word.speaker_confidence is not None
            and word.speaker_confidence < 0.2
            and word.acoustic_support_source == "speaker_diarization"
            and support_start_ms is not None
            and support_end_ms is not None
            and support_end_ms > support_start_ms
            and support_end_ms - support_start_ms <= 2_000
        ):
            continue
        projected[word.word_id] = (support_start_ms, support_end_ms)
    return projected


def _unsupported_non_speech_run_cue_ids(
    cues: list[VideoLocalizationCue],
    *,
    word_by_id: dict[str, VideoLocalizationAlignedWord],
) -> list[str]:
    """Find long ASR-only runs that lack evidence of human speech.

    A failed forced alignment may leave invented words spread across music or
    silence.  One uncertain cue remains reviewable; only a long contiguous run
    is omitted, and only when every word is low-confidence interpolation,
    every cue lacks speaker evidence, and the implied word pace is far slower
    than ordinary speech.
    """

    candidate_runs: list[list[VideoLocalizationCue]] = []
    current: list[VideoLocalizationCue] = []
    for cue in cues:
        cue_words = [word_by_id[word_id] for word_id in cue.source_word_ids if word_id in word_by_id]
        flags = set(cue.quality_flags)
        unsupported = bool(
            cue_words
            and not cue.speaker_id
            and not cue.speaker_cluster_id
            and "timing_review_required" in flags
            and "segment_timing_interpolated" in flags
            and all(
                word.timing_confidence == "low"
                and word.timing_source == "asr_segment_interpolation"
                and not word.speaker_cluster_id
                for word in cue_words
            )
        )
        if unsupported:
            current.append(cue)
            continue
        if current:
            candidate_runs.append(current)
            current = []
    if current:
        candidate_runs.append(current)

    omitted: list[str] = []
    for run in candidate_runs:
        word_ids = [word_id for cue in run for word_id in cue.source_word_ids if word_id in word_by_id]
        if len(run) < 2 or not word_ids:
            continue
        wall_duration_ms = run[-1].end_ms - run[0].start_ms
        covered_duration_ms = sum(cue.end_ms - cue.start_ms for cue in run)
        mean_duration_per_word_ms = covered_duration_ms / len(word_ids)
        if wall_duration_ms >= 8_000 and mean_duration_per_word_ms >= 1_200:
            omitted.extend(cue.cue_id for cue in run)
    omitted_set = set(omitted)
    changed = True
    while changed and omitted_set:
        changed = False
        for index, cue in enumerate(cues):
            if cue.cue_id in omitted_set:
                continue
            if (
                cue.speaker_id
                or cue.speaker_cluster_id
                or cue.review_status != "needs_review"
                or "needs_speaker_assignment" not in cue.quality_flags
            ):
                continue
            adjacent_to_omitted = bool(
                index > 0 and cues[index - 1].cue_id in omitted_set and cue.start_ms - cues[index - 1].end_ms <= 750
            ) or bool(
                index + 1 < len(cues)
                and cues[index + 1].cue_id in omitted_set
                and cues[index + 1].start_ms - cue.end_ms <= 750
            )
            if adjacent_to_omitted:
                omitted_set.add(cue.cue_id)
                changed = True
    return [cue.cue_id for cue in cues if cue.cue_id in omitted_set]


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"{label} 不能重复：" + "、".join(duplicates))


def _require_ordered_timing(
    items: list[LocalizationSourceCue | LocalizationSourceWord | LocalizationSourcePause],
    label: str,
    *,
    allow_zero_width: bool = False,
) -> None:
    for item in items:
        invalid = (
            item.end_ms < item.start_ms
            if isinstance(item, LocalizationSourcePause) or allow_zero_width
            else item.end_ms <= item.start_ms
        )
        if invalid:
            source_id = getattr(
                item,
                "cue_id",
                getattr(item, "word_id", getattr(item, "boundary_id", "")),
            )
            raise ValueError(f"{label} {source_id} 缺少有效时间。")
    if any(current.start_ms < previous.start_ms for previous, current in zip(items, items[1:])):
        raise ValueError(f"{label} 必须按时间顺序排列。")


def _fingerprint(value: object) -> str:
    payload = _jsonable(value)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _even_samples(
    values: list[LocalizationSourceCue],
    *,
    limit: int,
) -> list[LocalizationSourceCue]:
    if limit <= 0 or not values:
        return []
    if len(values) <= limit:
        return list(values)
    if limit == 1:
        return [values[len(values) // 2]]
    indexes = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
    return [values[index] for index in dict.fromkeys(indexes)]


__all__ = [
    "DEFAULT_LOCALIZATION_PIPELINE",
    "SOURCE_CONFIDENCE_INTERPRETATION_POLICY",
    "LocalizationPipeline",
    "LocalizationSourceComponentFingerprints",
    "LocalizationSourceCue",
    "LocalizationSourceGlossaryEntry",
    "LocalizationSourceLockInput",
    "LocalizationSourceLockQualitySummary",
    "LocalizationSourceLockResult",
    "LocalizationSourceLockWarning",
    "LocalizationSourcePause",
    "LocalizationSourceSpeaker",
    "LocalizationSourceWord",
    "build_localization_source_input",
    "project_localization_source_lock_step_result",
    "project_localization_source_quality_flags",
]
