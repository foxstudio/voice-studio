from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    now_iso,
)
from app.errors import AppException
from app.services import llm_runtime, settings_store, web_search


LOCALIZATION_PROMPT_VERSION = "localization-draft-v5"
LOCALIZATION_BATCH_MAX_CUES = 64
LOCALIZATION_BATCH_MAX_WORDS = 280
LOCALIZATION_BATCH_MAX_SOURCE_CHARS = 3200
QUALITY_REVIEW_BATCH_MAX_ITEMS = 60
QUALITY_REVIEW_BATCH_MAX_TEXT_CHARS = 12_000
QUALITY_REVIEW_MAX_REQUESTS = 8
QUALITY_REVIEW_MAX_SECONDS = 600
LOCALIZATION_FIT_BATCH_MAX_ITEMS = 12
LOCALIZATION_FIT_MAX_ROUNDS = 3
LOCALIZATION_FIT_MAX_REQUESTS = 16
LOCALIZATION_FIT_MAX_SECONDS = 600
MIN_SUBTITLE_DURATION_MS = 833
MAX_SUBTITLE_DURATION_MS = 7000
MAX_CHARS_PER_LINE = 16
MAX_CHINESE_CPS = 9.0
END_HOLD_MS = 220
PREFERRED_PAUSE_SPLIT_MIN_GAP_MS = 280
PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS = 18
NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)*(?:%|％|[KkDd])?")
DISPLAY_PUNCTUATION = frozenset(",，。.;；:：")

ProgressCallback = Callable[[float, str], None]
PreviewCallback = Callable[[str, list[dict]], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class LocalizationRun:
    draft: VideoLocalizationDraft
    summary: dict


def source_fingerprint(draft: VideoLocalizationDraft) -> str:
    payload = {
        "transcription_revision_ids": sorted(
            {cue.transcription_revision_id for cue in draft.cues if cue.transcription_revision_id}
        ),
        "cues": [
            {
                "cue_id": cue.cue_id,
                "speaker_id": cue.speaker_id,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "text": cue.en_subtitle_text,
                "source_word_ids": cue.source_word_ids,
            }
            for cue in draft.cues
        ],
        "speakers": [
            {
                "speaker_id": speaker.speaker_id,
                "display_name": speaker.display_name,
                "notes": speaker.notes,
            }
            for speaker in sorted(draft.speakers, key=lambda item: item.speaker_id)
        ],
        "transcription": (
            {
                "revision_id": draft.transcription.revision_id,
                "language": draft.transcription.language,
                "source_track_id": draft.transcription.source_track_id,
                "source_audio_sha256": draft.transcription.source_audio_sha256,
                "words": [
                    {
                        "word_id": word.word_id,
                        "segment_id": word.segment_id,
                        "text": word.text,
                        "start_ms": word.start_ms,
                        "end_ms": word.end_ms,
                        "timing_source": word.timing_source,
                    }
                    for word in draft.transcription.words
                ],
            }
            if draft.transcription
            else None
        ),
        "glossary": [item.model_dump(mode="json") for item in draft.glossary],
        "scene_context": draft.scene_context,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_localization_draft(
    draft: VideoLocalizationDraft,
    *,
    source_language: str = "en",
    target_language: str = "zh-Hans",
    profile_id: str | None = None,
    localization_level: str = "L1",
    worldview_permeability: str = "W0",
    is_cancelled: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_preview: PreviewCallback | None = None,
) -> LocalizationRun:
    _validate_source(draft)
    if target_language != "zh-Hans":
        raise AppException(400, "VIDEO_LOCALIZATION_TARGET_LANGUAGE_UNSUPPORTED", "当前版本先支持简体中文本土化。")

    profile = llm_runtime.resolve_profile(profile_id)
    fingerprint = source_fingerprint(draft)
    step_results: dict[str, dict] = {}
    started_at = time.perf_counter()

    _report(on_progress, 0.06, "正在理解原文与人物")
    _ensure_active(is_cancelled)
    context = _analyze_context(
        draft,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        localization_level=localization_level,
        worldview_permeability=worldview_permeability,
    )
    step_results["prepare_context"] = _context_step_result(context, draft)

    _report(on_progress, 0.18, "正在查证文化与背景")
    _ensure_active(is_cancelled)
    research = _research_context(context, is_cancelled=is_cancelled)

    _report(on_progress, 0.30, "正在生成中文表达")
    candidates = _localize_cues(
        draft,
        context=context,
        research=research,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        localization_level=localization_level,
        worldview_permeability=worldview_permeability,
        is_cancelled=is_cancelled,
        on_batch=lambda completed, total: _report(
            on_progress,
            0.30 + 0.28 * completed / max(1, total),
            f"正在生成中文表达 · {completed}/{total}",
        ),
        on_repair=lambda batch_number, total: _report(
            on_progress,
            0.30 + 0.28 * max(0, batch_number - 1) / max(1, total),
            f"正在生成中文表达 · 核对第 {batch_number}/{total} 批数字",
        ),
        on_split=lambda batch_number, total: _report(
            on_progress,
            0.30 + 0.28 * max(0, batch_number - 1) / max(1, total),
            f"正在生成中文表达 · 第 {batch_number}/{total} 批内容较长，正在拆分",
        ),
        on_batch_preview=(
            (lambda items: on_preview("localized_draft", _rough_preview(items, draft))) if on_preview else None
        ),
    )
    initial_candidates = candidates
    fit_diagnostics: dict = {}
    _report(on_progress, 0.59, "正在调整字幕长度 · 准备处理")
    candidates = _fit_candidate_segments(
        candidates,
        draft,
        context=context,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        is_cancelled=is_cancelled,
        on_progress=lambda completed, total, round_number, detail: _report(
            on_progress,
            0.59 + 0.08 * ((round_number - 1) + completed / max(1, total)) / LOCALIZATION_FIT_MAX_ROUNDS,
            f"正在调整字幕长度 · 第 {round_number} 轮 {completed}/{total} · {detail}",
        ),
        on_preview=(
            (
                lambda items: on_preview(
                    "localized_fit",
                    [_preview_item(item) for item in _time_candidates(items, draft)],
                )
            )
            if on_preview
            else None
        ),
        diagnostics=fit_diagnostics,
    )
    step_results["research"] = _research_step_result(research, candidates)
    step_results["localize"] = _localize_step_result(initial_candidates, draft)
    step_results["fit_segments"] = _fit_step_result(initial_candidates, candidates, fit_diagnostics)
    if on_preview and candidates != initial_candidates:
        on_preview("localized_fit", [_preview_item(item) for item in _time_candidates(candidates, draft)])

    _report(on_progress, 0.68, "正在安排字幕分段与时间")
    _ensure_active(is_cancelled)
    timed = _finalize_timing(_time_candidates(candidates, draft), draft)
    step_results["segment_timing"] = _timing_step_result(timed)
    if on_preview:
        on_preview("localized_timing", [_preview_item(item) for item in timed])

    _report(on_progress, 0.75, "正在复核语义与可读性")
    review_diagnostics: dict = {}
    reviewed, review_changes = _quality_review(
        timed,
        draft=draft,
        context=context,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        is_cancelled=is_cancelled,
        on_batch=lambda completed, total: _report(
            on_progress,
            0.75 + 0.16 * completed / max(1, total),
            f"正在复核语义与可读性 · {completed}/{total}",
        ),
        on_split=lambda batch_number, total: _report(
            on_progress,
            0.75 + 0.16 * max(0, batch_number - 1) / max(1, total),
            f"正在复核语义与可读性 · 第 {batch_number}/{total} 批内容较长，正在拆分",
        ),
        diagnostics=review_diagnostics,
    )
    reviewed = _finalize_timing(reviewed, draft)
    step_results["quality_review"] = _quality_step_result(reviewed, review_changes, review_diagnostics)

    # A wording correction in final review can make an otherwise valid cue too dense.
    # Reuse the constrained fitter only when the deterministic budget check finds a regression.
    _report(on_progress, 0.92, "正在确认终审后的字幕限制")
    post_review_diagnostics: dict = {}
    post_review_input = reviewed
    reviewed = _fit_candidate_segments(
        reviewed,
        draft,
        context=context,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        is_cancelled=is_cancelled,
        include_preferred_pause_splits=False,
        on_progress=lambda completed, total, round_number, detail: _report(
            on_progress,
            0.92 + 0.015 * ((round_number - 1) + completed / max(1, total)) / LOCALIZATION_FIT_MAX_ROUNDS,
            f"正在确认终审后的字幕限制 · 第 {round_number} 轮 {completed}/{total} · {detail}",
        ),
        diagnostics=post_review_diagnostics,
    )
    reviewed = _finalize_timing(_time_candidates(reviewed, draft), draft)
    step_results["post_review_constraints"] = _post_review_constraint_step_result(
        post_review_input,
        reviewed,
        post_review_diagnostics,
    )
    if on_preview:
        on_preview("localized_review", [_preview_item(item) for item in reviewed])

    _report(on_progress, 0.94, "正在写入本土化字幕轨")
    _ensure_active(is_cancelled)
    next_draft = _with_localized_track(
        draft,
        reviewed,
        fingerprint=fingerprint,
        context=context,
        research=research,
        source_language=source_language,
        target_language=target_language,
        profile_id=profile.profile_id,
        model_id=profile.model_id,
        localization_level=localization_level,
        worldview_permeability=worldview_permeability,
    )
    step_results["write_track"] = _write_step_result(next_draft.localized_subtitles)
    _report(on_progress, 0.98, "本土化字幕初稿已生成，正在保存")

    return LocalizationRun(
        draft=next_draft,
        summary={
            "source_language": source_language,
            "target_language": target_language,
            "source_fingerprint": fingerprint,
            "llm_profile_id": profile.profile_id,
            "llm_model_id": profile.model_id,
            "localized_subtitle_count": len(next_draft.localized_subtitles),
            "duration_ms": _elapsed_ms(started_at),
            "task_step_results": step_results,
            "preview_phase": "localized_review",
            "preview_cues": [_preview_item(item) for item in reviewed],
        },
    )


def with_chinese_draft(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    """Compatibility entry point for the legacy synchronous endpoint."""
    return generate_localization_draft(draft).draft


def _validate_source(draft: VideoLocalizationDraft) -> None:
    usable = [
        cue
        for cue in draft.cues
        if (cue.en_subtitle_text or "").strip() and cue.start_ms is not None and cue.end_ms is not None
    ]
    if not usable:
        raise AppException(400, "VIDEO_LOCALIZATION_CUES_MISSING", "请先生成并校对 ASR 字幕。")
    if any(cue.end_ms is None or cue.start_ms is None or cue.end_ms <= cue.start_ms for cue in usable):
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_TIMING_INVALID", "ASR 字幕中存在无效时间，请先修正。")


def _analyze_context(
    draft: VideoLocalizationDraft,
    *,
    profile_id: str,
    source_language: str,
    target_language: str,
    localization_level: str,
    worldview_permeability: str,
) -> dict:
    payload = {
        "task": f"{LOCALIZATION_PROMPT_VERSION}:context",
        "source_language": source_language,
        "target_language": target_language,
        "localization_level": localization_level,
        "worldview_permeability": worldview_permeability,
        "scene_context": draft.scene_context.strip()[:3000] or None,
        "speakers": [
            {"speaker_id": speaker.speaker_id, "name": speaker.display_name, "notes": speaker.notes}
            for speaker in draft.speakers
        ],
        "glossary": [item.model_dump(mode="json") for item in draft.glossary],
        "transcript": [
            {"cue_id": cue.cue_id, "speaker_id": cue.speaker_id, "text": cue.en_subtitle_text}
            for cue in draft.cues
            if (cue.en_subtitle_text or "").strip()
        ],
        "output": (
            "返回 overview、era、setting、topics、speakers、style_rules、needs_research、research_questions。"
            "speakers 每项包含 speaker_id、persona、speech_habits、relationship、emotion。"
            "research_questions 每项包含 query、reason、category、target_terms；只有外部资料能减少误译时才提出。"
        ),
    }
    raw = llm_runtime.complete_json(
        system_prompt=(
            "你是影视本土化总编。先理解内容、时代、场景、人物关系、说话习惯与情绪，再决定哪些事实或文化背景需要外部查证。"
            "人物口吻来自原文证据，不得凭空编造。把本土化强度与世界观渗透程度分开考虑。只返回约定 JSON。"
        ),
        user_payload=payload,
        profile_id=profile_id,
        temperature=0.1,
        max_tokens=5000,
        timeout=120,
    )
    if not isinstance(raw, dict):
        raise AppException(422, "VIDEO_LOCALIZATION_CONTEXT_INVALID", "语言模型没有返回可用的内容理解结果。")
    return {
        "overview": _text(raw.get("overview"), 1200),
        "era": _text(raw.get("era"), 200),
        "setting": _text(raw.get("setting"), 300),
        "topics": _string_list(raw.get("topics"), 12, 120),
        "speakers": _dict_list(raw.get("speakers"), 24),
        "style_rules": _string_list(raw.get("style_rules"), 16, 300),
        "needs_research": bool(raw.get("needs_research")),
        "research_questions": _dict_list(raw.get("research_questions"), 8),
    }


def _research_context(context: dict, *, is_cancelled: CancelCallback | None) -> dict:
    questions = context.get("research_questions") if context.get("needs_research") else []
    settings = settings_store.web_search_settings()
    if not questions:
        return {"status": "not_needed", "reason": "现有原文与项目资料足以完成初稿", "questions": []}
    if not settings.enabled:
        return {"status": "disabled", "reason": "网络查证未启用，继续使用项目内资料", "questions": []}

    api_key = settings_store.web_search_api_key()
    items = []
    failures = 0
    for raw in questions[: settings.max_queries]:
        _ensure_active(is_cancelled)
        query = _text(raw.get("query"), 240)
        if not query:
            continue
        try:
            results = web_search.search(settings, query, api_key=api_key)
            error = None
        except Exception as exc:
            results = []
            error = str(exc)[:300]
            failures += 1
        items.append(
            {
                "question_id": f"research_{len(items) + 1:02d}",
                "query": query,
                "reason": _text(raw.get("reason"), 500),
                "category": _text(raw.get("category"), 80) or "背景",
                "target_terms": _string_list(raw.get("target_terms"), 8, 100),
                "sources": [
                    {"title": item.title, "url": item.url, "snippet": item.snippet, "provider": settings.provider}
                    for item in results
                ],
                "error": error,
            }
        )
    source_count = sum(len(item["sources"]) for item in items)
    status = "completed" if not failures else "partial" if source_count else "failed"
    return {"status": status, "reason": "只查证会影响理解或文化转述的问题", "questions": items}


def _localization_batches(cues: list[VideoLocalizationCue]) -> list[tuple[int, list[VideoLocalizationCue]]]:
    batches: list[tuple[int, list[VideoLocalizationCue]]] = []
    current: list[VideoLocalizationCue] = []
    current_start = 0
    current_words = 0
    current_chars = 0
    for cue_index, cue in enumerate(cues):
        cue_words, cue_chars = _localization_cue_weight(cue)
        if cue_words > LOCALIZATION_BATCH_MAX_WORDS or cue_chars > LOCALIZATION_BATCH_MAX_SOURCE_CHARS:
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_SOURCE_CUE_TOO_LARGE",
                "一条原文字幕包含的内容过长，无法稳定生成本土化字幕。请先重新听写或拆分这条原文字幕。",
                {"cue_id": cue.cue_id, "word_count": cue_words, "character_count": cue_chars},
            )
        exceeds_batch = current and (
            len(current) >= LOCALIZATION_BATCH_MAX_CUES
            or current_words + cue_words > LOCALIZATION_BATCH_MAX_WORDS
            or current_chars + cue_chars > LOCALIZATION_BATCH_MAX_SOURCE_CHARS
        )
        if exceeds_batch:
            batches.append((current_start, current))
            current = []
            current_start = cue_index
            current_words = 0
            current_chars = 0
        current.append(cue)
        current_words += cue_words
        current_chars += cue_chars
    if current:
        batches.append((current_start, current))
    if len(batches) >= 2 and len(batches[-1][1]) < max(2, LOCALIZATION_BATCH_MAX_CUES // 4):
        previous_start, previous = batches[-2]
        _last_start, last = batches[-1]
        combined = [*previous, *last]
        valid_splits = [
            split
            for split in range(1, len(combined))
            if _localization_batch_fits(combined[:split]) and _localization_batch_fits(combined[split:])
        ]
        if valid_splits:
            split = min(valid_splits, key=lambda value: abs(len(combined) - 2 * value))
            batches[-2:] = [
                (previous_start, combined[:split]),
                (previous_start + split, combined[split:]),
            ]
    return batches


def _localization_cue_weight(cue: VideoLocalizationCue) -> tuple[int, int]:
    source_text = (cue.en_subtitle_text or "").strip()
    words = len(cue.source_word_ids) or max(1, len(re.findall(r"\w+|[^\w\s]", source_text)))
    return words, len(source_text)


def _localization_batch_fits(cues: list[VideoLocalizationCue]) -> bool:
    weights = [_localization_cue_weight(cue) for cue in cues]
    return (
        len(cues) <= LOCALIZATION_BATCH_MAX_CUES
        and sum(words for words, _chars in weights) <= LOCALIZATION_BATCH_MAX_WORDS
        and sum(chars for _words, chars in weights) <= LOCALIZATION_BATCH_MAX_SOURCE_CHARS
    )


def _localize_cues(
    draft: VideoLocalizationDraft,
    *,
    context: dict,
    research: dict,
    profile_id: str,
    source_language: str,
    target_language: str,
    localization_level: str,
    worldview_permeability: str,
    is_cancelled: CancelCallback | None,
    on_batch: Callable[[int, int], None],
    on_batch_preview: Callable[[list[dict]], None] | None = None,
    on_repair: Callable[[int, int], None] | None = None,
    on_split: Callable[[int, int], None] | None = None,
) -> list[dict]:
    cues = [cue for cue in draft.cues if (cue.en_subtitle_text or "").strip()]
    cue_by_id = {cue.cue_id: cue for cue in cues}
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    allowed_research_ids = {
        str(item.get("question_id")) for item in research.get("questions") or [] if item.get("question_id")
    }
    batches = _localization_batches(cues)
    candidates: list[dict] = []
    for batch_index, (batch_start, batch) in enumerate(batches):
        _ensure_active(is_cancelled)
        allowed_ids = {cue.cue_id for cue in batch}
        allowed_word_ids = {word_id for cue in batch for word_id in cue.source_word_ids if word_id in word_by_id}
        payload = {
            "task": f"{LOCALIZATION_PROMPT_VERSION}:localize",
            "source_language": source_language,
            "target_language": target_language,
            "profile": {
                "localization_level": localization_level,
                "worldview_permeability": worldview_permeability,
                "audience": "中国大陆观众",
            },
            "context": context,
            "research": _research_payload(research),
            "glossary": [item.model_dump(mode="json") for item in draft.glossary],
            "neighbor_context": {
                "previous": [
                    {"cue_id": cue.cue_id, "text": cue.en_subtitle_text}
                    for cue in cues[max(0, batch_start - 2) : batch_start]
                ],
                "next": [
                    {"cue_id": cue.cue_id, "text": cue.en_subtitle_text}
                    for cue in cues[batch_start + len(batch) : batch_start + len(batch) + 2]
                ],
            },
            "source_cues": [_cue_payload(cue, word_by_id) for cue in batch],
            "pause_boundaries": _pause_boundary_payload(batch, word_by_id),
            "rules": {
                "immutable": ["事实", "数字", "专名", "否定", "因果", "比较", "人物意图", "情绪强度"],
                "display_text": "自然口语、单行优先、尽量不使用逗号句号等常规标点，只保留确有必要的问号或感叹号",
                "tts_text": "与上屏字幕含义一致，但保留语音合成需要的标点、停顿、语气词和口语节奏",
                "segmentation": (
                    "按完整语义和源音频停顿重新分段，不必与源 cue 一一对应，可合并或拆分。"
                    f"当一段预计超过 {PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS} 个可见中文字，且输入给出至少 "
                    f"{PREFERRED_PAUSE_SPLIT_MIN_GAP_MS} 毫秒的停顿边界时，只要边界两侧都能成为自然中文语义单位，优先拆成两段。"
                    "不要在专名、固定搭配、动宾结构、数量词、否定结构或因果连接中间硬拆。"
                    "第一次生成就必须满足上屏限制；超过限制时在这一轮直接按 source_word_ids 拆开，不要留给后续返修"
                ),
                "native_spoken_chinese": (
                    "先理解整句话的说话意图，再用中国大陆母语者真实口头表达重写。"
                    "允许调整语序、省略中文里多余的主语和将来时、把名词化结构改成短主动句；"
                    "逐项警惕‘我将、怎样、进行、创建、获得、实现、其、该、从而’等可能来自英文句法或书面稿的词，"
                    "只有在中文口头表达确实自然时才保留；避免‘我将向你展示怎样’‘进行创建’‘获得效果’等翻译腔。"
                ),
                "speaker_voice": (
                    "保留同一说话人的表达层级与个性，包括正式或随意、专业或外行、笃定或迟疑、冷静分析或强烈强调；"
                    "访谈、播客和对话还要保留轮次、接话、质疑、打断与自我修正，不能全部润色成统一顺滑的播音稿。"
                ),
                "discourse_markers": (
                    "语气词和口头填充词按功能处理而不是逐字翻译：表达犹豫、立场、转折、自我修正、与观众关系或人物习惯时，"
                    "改成自然中文并适量保留；只是 ASR 噪声或重复赘词时删除。不能为了显得口语化凭空添加‘其实、你知道吧、对吧’。"
                ),
                "cultural_function": (
                    "保留原作中的文化、地点、产品、头衔、笑点、比喻和典故；需要时用简短自然表达让中国观众听懂，"
                    "但只能做功能等价转述，不能替换成无关的中国人物、地名、网络梗或额外事实。"
                ),
                "lexical_context": (
                    "术语和形容词必须按当前对象与行业语境翻译，不能只取词典首义；"
                    "优先说出观众能直接感知的结果，区分画面清晰、自然、少噪点/少瑕疵、动作流畅、结果可直接使用等不同含义。"
                    "例如英文 clean 在 AI 画面语境不能机械写成‘干净’，应按上下文选择清晰、自然、没瑕疵或可直接用。"
                ),
                "cohesion": (
                    "检查中文固定搭配、动词补语、介词和指代是否完整；"
                    "根据真实指代而不是英文代词字面区分人物的他/她与产品、模型、画面、提示词等非人物的它；"
                    "如果上下文明确指向工具或 AI，即使 ASR 原文误写 he/she，也应改为它或省略代词。"
                ),
                "hard_display_limits": {
                    "max_duration_ms": MAX_SUBTITLE_DURATION_MS,
                    "max_visible_chars": MAX_CHARS_PER_LINE * 2,
                    "max_chinese_chars_per_second": MAX_CHINESE_CPS,
                    "calculation": "片段时长取首个 source_word_id 的 start_ms 到末个 source_word_id 的 end_ms",
                },
                "adaptation_note": "用简短中文说明本土化处理；专名或代码可保留原文，不使用内部字段名或英文流程术语",
                "research_usage": "只有查证资料实际影响当前表达时才填写；没有直接影响必须返回空数组",
            },
            "output": (
                "返回 segments 数组。每项必须包含 source_cue_ids、source_word_ids、display_text、tts_text、adaptation_note、research_usage。"
                "research_usage 每项包含 question_id 和 effect，说明哪项查证具体改变或确认了什么；未直接采用资料时必须为空数组。"
                "source_cue_ids 和 source_word_ids 只能使用输入中的 id，并按原文顺序覆盖本批全部 cue。"
                "输出前逐段检查时长、可见字数和阅读速度，不能返回需要再次拆分的超限长段。"
            ),
        }

        def split_current_batch() -> list[dict]:
            if len(batch) <= 1:
                raise AppException(422, "VIDEO_LOCALIZATION_LOCALIZATION_INVALID", "单条本土化字幕仍无法通过校验。")
            if on_split:
                on_split(batch_index + 1, len(batches))
            midpoint = len(batch) // 2
            split_items: list[dict] = []
            for sub_batch in (batch[:midpoint], batch[midpoint:]):
                sub_draft = draft.model_copy(update={"cues": sub_batch})
                sub_items = _localize_cues(
                    sub_draft,
                    context=context,
                    research=research,
                    profile_id=profile_id,
                    source_language=source_language,
                    target_language=target_language,
                    localization_level=localization_level,
                    worldview_permeability=worldview_permeability,
                    is_cancelled=is_cancelled,
                    on_batch=lambda _completed, _total: None,
                    on_batch_preview=(
                        (lambda items: on_batch_preview([*candidates, *split_items, *items]))
                        if on_batch_preview
                        else None
                    ),
                    on_repair=(
                        (lambda _batch_number, _total: on_repair(batch_index + 1, len(batches))) if on_repair else None
                    ),
                    on_split=(
                        (lambda _batch_number, _total: on_split(batch_index + 1, len(batches))) if on_split else None
                    ),
                )
                split_items.extend(sub_items)
            return split_items

        try:
            raw = llm_runtime.complete_json(
                system_prompt=(
                    "你是中文影视字幕本土化编辑，不是逐句翻译器。先通读上下文，保持说话行为、事实和人物口吻，"
                    "再改写成中国大陆创作者面对镜头时自然会说的口语。中文必须能直接说出口，不能保留英语语序、书面腔或生硬直译。"
                    "不要逐字硬译，不要擅自植入中国历史人物、地名、典故或网络热梗。上屏字幕与配音台词是两个用途不同但语义一致的文本。"
                    "源字幕行只是识别容器，不是目标语言分段边界。只返回约定 JSON。"
                ),
                user_payload=payload,
                profile_id=profile_id,
                temperature=0.18,
                max_tokens=16384,
                timeout=180,
                allow_array=True,
            )
        except llm_runtime.LlmRuntimeError as exc:
            if exc.code != "llm_output_truncated" or len(batch) <= 1:
                raise
            parsed = split_current_batch()
            raw_segments = None
        else:
            raw_segments = raw if isinstance(raw, list) else raw.get("segments") if isinstance(raw, dict) else None
        if raw_segments is not None:
            try:
                parsed = _validate_localized_segments(
                    raw_segments,
                    batch,
                    allowed_ids,
                    allowed_word_ids,
                    cue_by_id,
                    word_by_id,
                    allowed_research_ids=allowed_research_ids,
                )
            except AppException as exc:
                if (
                    exc.code
                    in {
                        "VIDEO_LOCALIZATION_SOURCE_COVERAGE_INCOMPLETE",
                        "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID",
                        "VIDEO_LOCALIZATION_LOCALIZATION_INVALID",
                    }
                    and len(batch) > 1
                ):
                    parsed = split_current_batch()
                    exc = None
                elif exc.code != "VIDEO_LOCALIZATION_NUMBER_CHANGED":
                    raise
                if exc is not None:
                    _ensure_active(is_cancelled)
                    if on_repair:
                        on_repair(batch_index + 1, len(batches))
                    problem_ids = set(_string_list(exc.detail_dict.get("source_cue_ids"), 24, 120))
                    problem_segments = [
                        item
                        for item in (raw_segments or [])
                        if isinstance(item, dict)
                        and problem_ids.intersection(_string_list(item.get("source_cue_ids"), 24, 120))
                    ]
                    problem_cues = [cue for cue in batch if cue.cue_id in problem_ids]
                    repair_payload = {
                        "task": f"{LOCALIZATION_PROMPT_VERSION}:repair-numbers",
                        "source_language": source_language,
                        "target_language": target_language,
                        "context": context,
                        "source_cues": [_cue_payload(cue, word_by_id) for cue in problem_cues],
                        "previous_segments": problem_segments,
                        "required_numbers": [
                            {
                                "cue_id": cue.cue_id,
                                "tokens": list(_normalized_numbers(cue.en_subtitle_text or "").elements()),
                            }
                            for cue in problem_cues
                            if _normalized_numbers(cue.en_subtitle_text or "")
                        ],
                        "repair_instruction": (
                            "只修正数字遗漏或改写。每个数字、百分比、版本号和规格型号必须在 display_text 和 tts_text 中按原字符出现；"
                            "不得增加原文没有的阿拉伯数字。保持原有 source_cue_ids、source_word_ids、语义和分段。"
                        ),
                        "output": "只返回修正后的问题 segments，不要返回本批其他字幕。",
                    }
                    repaired = llm_runtime.complete_json(
                        system_prompt=(
                            "你是字幕事实校对员。上一版本土化字幕的数字与原文不一致。"
                            "只修复数字和规格，不改变其他翻译、分段或来源关系，只返回约定 JSON。"
                        ),
                        user_payload=repair_payload,
                        profile_id=profile_id,
                        temperature=0.0,
                        max_tokens=4096,
                        timeout=180,
                        allow_array=True,
                    )
                    repaired_segments = (
                        repaired
                        if isinstance(repaired, list)
                        else repaired.get("segments")
                        if isinstance(repaired, dict)
                        else None
                    )
                    repaired_by_source = {
                        tuple(_string_list(item.get("source_cue_ids"), 24, 120)): item
                        for item in (repaired_segments or [])
                        if isinstance(item, dict)
                    }
                    merged_segments = [
                        repaired_by_source.get(tuple(_string_list(item.get("source_cue_ids"), 24, 120)), item)
                        if isinstance(item, dict)
                        else item
                        for item in (raw_segments or [])
                    ]
                    try:
                        parsed = _validate_localized_segments(
                            merged_segments,
                            batch,
                            allowed_ids,
                            allowed_word_ids,
                            cue_by_id,
                            word_by_id,
                            allowed_research_ids=allowed_research_ids,
                        )
                    except AppException as repair_exc:
                        if (
                            repair_exc.code
                            in {
                                "VIDEO_LOCALIZATION_NUMBER_CHANGED",
                                "VIDEO_LOCALIZATION_SOURCE_COVERAGE_INCOMPLETE",
                                "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID",
                                "VIDEO_LOCALIZATION_LOCALIZATION_INVALID",
                            }
                            and len(batch) > 1
                        ):
                            parsed = split_current_batch()
                        else:
                            if repair_exc.code != "VIDEO_LOCALIZATION_NUMBER_CHANGED":
                                raise
                            raise AppException(
                                422,
                                repair_exc.code,
                                "数字自动核对后仍与原文不一致，任务已停止写入。",
                                repair_exc.detail_dict,
                            ) from repair_exc
        candidates.extend(parsed)
        on_batch(batch_index + 1, len(batches))
        if on_batch_preview:
            on_batch_preview(candidates)
    return candidates


def _validate_localized_segments(
    raw_segments,
    batch,
    allowed_ids,
    allowed_word_ids,
    cue_by_id,
    word_by_id,
    *,
    allowed_research_ids: set[str] | None = None,
) -> list[dict]:
    if not isinstance(raw_segments, list) or not raw_segments:
        raise AppException(422, "VIDEO_LOCALIZATION_LOCALIZATION_INVALID", "语言模型没有返回可用的本土化字幕。")
    parsed = []
    covered: set[str] = set()
    cue_order = {cue.cue_id: index for index, cue in enumerate(batch)}
    word_order = {word_id: index for index, word_id in enumerate(word_by_id)}
    previous_cue_position = -1
    returned_word_ids: list[str] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        source_cue_ids = [item for item in _string_list(raw.get("source_cue_ids"), 24, 120) if item in allowed_ids]
        source_word_ids = [
            item for item in _string_list(raw.get("source_word_ids"), 200, 120) if item in allowed_word_ids
        ]
        display_text = _normalize_display_text(_text(raw.get("display_text"), 800))
        tts_text = _text(raw.get("tts_text"), 1000) or display_text
        research_usage = [
            {"question_id": question_id, "effect": effect}
            for item in _dict_list(raw.get("research_usage"), 8)
            if (question_id := _text(item.get("question_id"), 80))
            and (allowed_research_ids is None or question_id in allowed_research_ids)
            and (effect := _text(item.get("effect"), 300))
        ]
        if not source_cue_ids or not display_text or not tts_text:
            continue
        cue_positions = [cue_order[cue_id] for cue_id in source_cue_ids]
        unique_positions = sorted(set(cue_positions))
        speaker_ids = {
            cue_by_id[cue_id].speaker_id
            for cue_id in source_cue_ids
            if cue_id in cue_by_id and cue_by_id[cue_id].speaker_id
        }
        if (
            cue_positions != sorted(cue_positions)
            or (unique_positions and unique_positions[-1] - unique_positions[0] + 1 != len(unique_positions))
            or (cue_positions and cue_positions[0] < previous_cue_position)
            or len(speaker_ids) > 1
        ):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID",
                "本土化字幕的原文范围顺序错误或跨越了不同说话人，任务已停止写入。",
                {"source_cue_ids": source_cue_ids, "speaker_ids": sorted(speaker_ids)},
            )
        if cue_positions:
            previous_cue_position = cue_positions[0]
        returned_word_ids.extend(source_word_ids)
        source_text = (
            _join_source_words([word_by_id[word_id].text for word_id in source_word_ids if word_id in word_by_id])
            if source_word_ids
            else " ".join((cue_by_id[item].en_subtitle_text or "").strip() for item in source_cue_ids)
        )
        if not _numbers_preserved(source_text, display_text) or not _numbers_preserved(source_text, tts_text):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_NUMBER_CHANGED",
                "本土化结果改变或遗漏了原文数字，正在尝试自动修正。",
                {
                    "source_numbers": list(_normalized_numbers(source_text).elements()),
                    "display_numbers": list(_normalized_numbers(display_text).elements()),
                    "tts_numbers": list(_normalized_numbers(tts_text).elements()),
                    "source_cue_ids": source_cue_ids,
                },
            )
        covered.update(source_cue_ids)
        parsed.append(
            {
                "id": f"localized_{len(parsed) + 1:04d}",
                "source_cue_ids": source_cue_ids,
                "source_word_ids": source_word_ids,
                "source_text": source_text,
                "display_text": display_text,
                "tts_text": tts_text,
                "adaptation_note": _text(raw.get("adaptation_note"), 600),
                "research_usage": research_usage,
                "quality_flags": [],
            }
        )
    missing = [cue.cue_id for cue in batch if cue.cue_id not in covered]
    expected_word_ids = [word_id for cue in batch for word_id in cue.source_word_ids if word_id in allowed_word_ids]
    returned_word_positions = [word_order[word_id] for word_id in returned_word_ids]
    word_mapping_invalid = bool(expected_word_ids) and (
        returned_word_ids != expected_word_ids
        or returned_word_positions != sorted(returned_word_positions)
        or len(returned_word_ids) != len(set(returned_word_ids))
    )
    if word_mapping_invalid:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID",
            "本土化字幕遗漏、重复或打乱了源语逐词时间，任务已停止写入。",
            {
                "expected_word_count": len(expected_word_ids),
                "returned_word_count": len(returned_word_ids),
            },
        )
    if not parsed or missing:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_COVERAGE_INCOMPLETE",
            "本土化结果遗漏了部分原文，任务已停止写入。",
            {"missing_cue_ids": missing[:20]},
        )
    return parsed


def _join_source_words(tokens: list[str]) -> str:
    result = ""
    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        if not result:
            result = token
        elif token[0].isdigit() and re.search(r"\d[.,]$", result):
            result += token
        elif token[0] in ".,!?;:，。！？；：)]}”’":
            result += token
        else:
            result += " " + token
    return result.strip()


def _time_candidates(candidates: list[dict], draft: VideoLocalizationDraft) -> list[dict]:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    timed = []
    for index, item in enumerate(candidates):
        words = [word_by_id[word_id] for word_id in item["source_word_ids"] if word_id in word_by_id]
        cues = [cue_by_id[cue_id] for cue_id in item["source_cue_ids"] if cue_id in cue_by_id]
        candidate_word_ids = {word.word_id for word in words}
        uncovered_cues = [
            cue for cue in cues if not any(word_id in candidate_word_ids for word_id in cue.source_word_ids)
        ]
        if words:
            start_ms = min([word.start_ms for word in words] + [int(cue.start_ms or 0) for cue in uncovered_cues])
            end_ms = max(
                [word.end_ms for word in words]
                + [int(cue.end_ms or start_ms + MIN_SUBTITLE_DURATION_MS) for cue in uncovered_cues]
            )
            timing_source = "逐词时间与 ASR 字幕范围" if uncovered_cues else "源音频逐词时间"
        else:
            start_ms = min(cue.start_ms or 0 for cue in cues)
            end_ms = max(cue.end_ms or start_ms + MIN_SUBTITLE_DURATION_MS for cue in cues)
            timing_source = "ASR 字幕时间范围"
        timed.append(
            {
                **item,
                "id": f"localized_{index + 1:04d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing_source": timing_source,
            }
        )

    _split_identical_ranges(timed)
    timed.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["id"]))
    for index, item in enumerate(timed):
        next_start = timed[index + 1]["start_ms"] if index + 1 < len(timed) else None
        if index and item["start_ms"] < timed[index - 1]["end_ms"]:
            item["start_ms"] = timed[index - 1]["end_ms"]
        desired_end = (
            min(item["end_ms"] + END_HOLD_MS, next_start) if next_start is not None else item["end_ms"] + END_HOLD_MS
        )
        item["end_ms"] = max(item["start_ms"] + 1, desired_end)
    return timed


def _fit_candidate_segments(
    candidates: list[dict],
    draft: VideoLocalizationDraft,
    *,
    context: dict,
    profile_id: str,
    source_language: str,
    target_language: str,
    is_cancelled: CancelCallback | None,
    include_preferred_pause_splits: bool = True,
    on_progress: Callable[[int, int, int, str], None] | None = None,
    on_preview: Callable[[list[dict]], None] | None = None,
    diagnostics: dict | None = None,
) -> list[dict]:
    fitted = list(candidates)
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    request_state = {"started_at": time.perf_counter(), "requests": 0}
    rounds: list[dict] = []

    for round_number in range(1, LOCALIZATION_FIT_MAX_ROUNDS + 1):
        timed = _time_candidates([{**item, "_fit_index": index} for index, item in enumerate(fitted)], draft)
        timed_by_index = {int(item["_fit_index"]): item for item in timed}
        problem_indexes = sorted(
            index
            for index, item in timed_by_index.items()
            if _candidate_exceeds_budget(item)
            or (
                include_preferred_pause_splits
                and round_number == 1
                and _candidate_has_preferred_pause_split(item, word_by_id)
            )
        )
        if not problem_indexes:
            _finish_fit_diagnostics(diagnostics, candidates, fitted, request_state, rounds)
            return fitted

        round_started_at = time.perf_counter()
        batches = [
            problem_indexes[index : index + LOCALIZATION_FIT_BATCH_MAX_ITEMS]
            for index in range(0, len(problem_indexes), LOCALIZATION_FIT_BATCH_MAX_ITEMS)
        ]
        replacements: dict[int, list[dict]] = {}
        for batch_index, indexes in enumerate(batches, start=1):
            _ensure_active(is_cancelled)
            if on_progress:
                on_progress(batch_index - 1, len(batches), round_number, f"正在处理 {len(indexes)} 段")
            batch_started_at = time.perf_counter()
            replacements.update(
                _refine_candidate_batch(
                    [(index, fitted[index], timed_by_index[index]) for index in indexes],
                    cue_by_id=cue_by_id,
                    word_by_id=word_by_id,
                    context=context,
                    profile_id=profile_id,
                    source_language=source_language,
                    target_language=target_language,
                    is_cancelled=is_cancelled,
                    request_state=request_state,
                )
            )
            if on_progress:
                elapsed_seconds = max(1, round(time.perf_counter() - batch_started_at))
                on_progress(batch_index, len(batches), round_number, f"本组耗时 {elapsed_seconds} 秒")
            if on_preview:
                on_preview(_apply_fit_replacements(fitted, replacements))

        next_fitted = _apply_fit_replacements(fitted, replacements)
        expected_word_ids = [word_id for item in fitted for word_id in item["source_word_ids"]]
        returned_word_ids = [word_id for item in next_fitted for word_id in item["source_word_ids"]]
        if expected_word_ids and returned_word_ids != expected_word_ids:
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_FIT_WORD_COVERAGE_INVALID",
                "字幕长度调整结果遗漏、重复或打乱了源语逐词时间，任务已停止写入。",
                {
                    "expected_word_count": len(expected_word_ids),
                    "returned_word_count": len(returned_word_ids),
                    "round": round_number,
                },
            )
        fitted = next_fitted
        rounds.append(
            {
                "round": round_number,
                "problem_count": len(problem_indexes),
                "batch_count": len(batches),
                "duration_ms": _elapsed_ms(round_started_at),
            }
        )

    remaining = [item for item in _time_candidates(fitted, draft) if _candidate_exceeds_budget(item)]
    if remaining:
        _finish_fit_diagnostics(diagnostics, candidates, fitted, request_state, rounds, unresolved_count=len(remaining))
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_TIMING_BUDGET_UNRESOLVED",
            "部分本土化字幕在自动调整后仍过长，任务已停止写入。",
            {
                "subtitle_ids": [item["id"] for item in remaining[:20]],
                "count": len(remaining),
                "limits": {
                    "max_duration_ms": MAX_SUBTITLE_DURATION_MS,
                    "max_chars": MAX_CHARS_PER_LINE * 2,
                    "max_cps": MAX_CHINESE_CPS,
                },
                "remaining": [
                    {
                        "subtitle_id": item["id"],
                        "source_cue_ids": item["source_cue_ids"],
                        **_candidate_budget_report(item),
                    }
                    for item in remaining[:20]
                ],
            },
        )
    _finish_fit_diagnostics(diagnostics, candidates, fitted, request_state, rounds)
    return fitted


def _apply_fit_replacements(fitted: list[dict], replacements: dict[int, list[dict]]) -> list[dict]:
    result: list[dict] = []
    for index, item in enumerate(fitted):
        result.extend(replacements.get(index, [item]))
    return result


def _finish_fit_diagnostics(
    diagnostics: dict | None,
    before: list[dict],
    after: list[dict],
    request_state: dict,
    rounds: list[dict],
    *,
    unresolved_count: int = 0,
) -> None:
    if diagnostics is None:
        return
    diagnostics.update(
        {
            "input_count": len(before),
            "output_count": len(after),
            "request_count": int(request_state["requests"]),
            "round_count": len(rounds),
            "duration_ms": _elapsed_ms(float(request_state["started_at"])),
            "unresolved_count": unresolved_count,
            "rounds": rounds,
        }
    )


def _candidate_exceeds_budget(item: dict) -> bool:
    return bool(_candidate_budget_report(item)["violations"])


def _candidate_has_preferred_pause_split(item: dict, word_by_id: dict) -> bool:
    if _readable_chars(item.get("display_text") or "") < PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS:
        return False
    ordered = [word_by_id[word_id] for word_id in item.get("source_word_ids") or [] if word_id in word_by_id]
    if len(ordered) < 4:
        return False
    return any(
        int(right.start_ms) - int(left.end_ms) >= PREFERRED_PAUSE_SPLIT_MIN_GAP_MS
        for left, right in zip(ordered[1:-2], ordered[2:-1])
    )


def _candidate_budget_report(item: dict) -> dict:
    duration = max(1, int(item["end_ms"]) - int(item["start_ms"]))
    chars = _readable_chars(item["display_text"])
    cps = chars * 1000 / duration
    violations = []
    if duration > MAX_SUBTITLE_DURATION_MS:
        violations.append("时长超过7秒，需要按完整语义拆分")
    if chars > MAX_CHARS_PER_LINE * 2:
        violations.append("上屏文字超过32个字，需要拆分或精简")
    if cps > MAX_CHINESE_CPS:
        violations.append("阅读速度超过每秒9字，需要在不丢信息的前提下精简表达")
    return {
        "duration_ms": duration,
        "visible_chars": chars,
        "cps": round(cps, 2),
        "max_chars_for_duration": max(
            1,
            min(MAX_CHARS_PER_LINE * 2, math.floor(duration * MAX_CHINESE_CPS / 1000)),
        ),
        "suggested_min_segments": max(
            1,
            math.ceil(duration / MAX_SUBTITLE_DURATION_MS),
            math.ceil(chars / (MAX_CHARS_PER_LINE * 2)),
        ),
        "violations": violations,
    }


def _refine_candidate_batch(
    entries: list[tuple[int, dict, dict]],
    *,
    cue_by_id: dict[str, VideoLocalizationCue],
    word_by_id: dict,
    context: dict,
    profile_id: str,
    source_language: str,
    target_language: str,
    is_cancelled: CancelCallback | None,
    request_state: dict,
    validation_retry: bool = False,
) -> dict[int, list[dict]]:
    _ensure_active(is_cancelled)
    payload_items = []
    for index, item, timed in entries:
        source_cues = _fit_source_cues(item, cue_by_id, word_by_id)
        budget = _candidate_budget_report(timed)
        payload_items.append(
            {
                "parent_id": f"candidate_{index:04d}",
                "current": {
                    "display_text": item["display_text"],
                    "tts_text": item["tts_text"],
                    "adaptation_note": item.get("adaptation_note"),
                    **budget,
                },
                "boundary_mode": "word" if _fit_uses_word_boundaries(source_cues, word_by_id) else "cue",
                "source_cues": [_cue_payload(cue, word_by_id) for cue in source_cues],
                "pause_boundaries": _pause_boundary_payload(source_cues, word_by_id),
                "refinement_goal": (
                    "存在清晰音频停顿且中文偏长；仅在停顿两侧语义都自然完整时拆分，拆后尽量降低单条字数"
                    if _candidate_has_preferred_pause_split(timed, word_by_id)
                    else "修复列出的字幕硬限制"
                ),
            }
        )

    timeout = _claim_fit_request(request_state)
    try:
        raw = llm_runtime.complete_json(
            system_prompt=(
                "你是中文影视字幕分段编辑。处理过长、阅读过快，或存在清晰音频停顿且中文偏长的本土化字幕。"
                "按完整语义、源语逐词时间和自然停顿拆分，必要时压缩中文，但不得删改事实、数字、专名、否定、因果或人物语气。"
                "对只有停顿拆分建议、没有硬性超限的项目，必须先判断停顿两侧是否都能成为自然中文语义单位；"
                "合适就拆以降低单条字数，不合适则原样保留，绝不能切断固定搭配、动宾结构、数量词或因果关系。"
                "代码已经列出每条字幕违反的硬性限制和建议最少段数。时长或字数超限时优先按语义拆分；"
                "只有阅读速度超限时，拆分不能解决问题，必须在不损失信息的前提下精简中文表达。"
                "每段上屏字幕不超过32个可见字符，源语时间范围不超过7秒，阅读速度不超过9字/秒。"
                "配音台词与上屏字幕含义一致，并保留语音合成需要的标点和语气。只返回约定 JSON。"
                + (
                    "上次结果没有通过结构或事实校验。请逐项检查 parent_id、结束位置、数字和文本后，完整返回整批结果。"
                    if validation_retry
                    else ""
                )
            ),
            user_payload={
                "task": f"{LOCALIZATION_PROMPT_VERSION}:fit-segments",
                "validation_retry": validation_retry,
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
                "limits": {
                    "max_duration_ms": MAX_SUBTITLE_DURATION_MS,
                    "max_display_chars": MAX_CHARS_PER_LINE * 2,
                    "max_display_cps": MAX_CHINESE_CPS,
                    "min_duration_ms": MIN_SUBTITLE_DURATION_MS,
                },
                "items": payload_items,
                "boundary_contract": {
                    "with_words": (
                        "每段只返回 end_word_id，表示该段包含到哪个词为止。结束词必须按输入顺序严格递增，"
                        "最后一段必须以该 parent 最后一个词结束"
                    ),
                    "without_words": (
                        "没有逐词数据时，每段只返回 end_cue_id。结束 cue 必须按输入顺序严格递增，"
                        "最后一段必须以该 parent 最后一个 cue 结束"
                    ),
                    "source_mapping": "不要返回 source_word_ids 或 source_cue_ids，连续来源范围由代码自动补齐",
                },
                "output": (
                    "返回 items 数组，顺序与 parent_id 必须和输入一致。每项包含 parent_id 和 segments。"
                    "严格按每项 boundary_mode：word 使用 end_word_id，cue 使用 end_cue_id。"
                    "每个 segment 还必须包含 display_text、tts_text、adaptation_note。"
                    "不要回传冗长的来源 ID 数组；每个 parent 必须连续覆盖到最后一个词或 cue。"
                ),
            },
            profile_id=profile_id,
            temperature=0.08,
            max_tokens=12000,
            timeout=timeout,
            allow_array=True,
        )
    except llm_runtime.LlmRuntimeError as exc:
        if exc.code != "llm_output_truncated" or len(entries) <= 1:
            raise
        midpoint = len(entries) // 2
        return {
            **_refine_candidate_batch(
                entries[:midpoint],
                cue_by_id=cue_by_id,
                word_by_id=word_by_id,
                context=context,
                profile_id=profile_id,
                source_language=source_language,
                target_language=target_language,
                is_cancelled=is_cancelled,
                request_state=request_state,
                validation_retry=validation_retry,
            ),
            **_refine_candidate_batch(
                entries[midpoint:],
                cue_by_id=cue_by_id,
                word_by_id=word_by_id,
                context=context,
                profile_id=profile_id,
                source_language=source_language,
                target_language=target_language,
                is_cancelled=is_cancelled,
                request_state=request_state,
                validation_retry=validation_retry,
            ),
        }

    raw_items = raw if isinstance(raw, list) else raw.get("items") if isinstance(raw, dict) else None
    expected_parent_ids = [f"candidate_{index:04d}" for index, _item, _timed in entries]
    returned_parent_ids = (
        [str(item.get("parent_id")) for item in raw_items if isinstance(item, dict)]
        if isinstance(raw_items, list)
        else []
    )
    if returned_parent_ids != expected_parent_ids:
        if not validation_retry:
            return _refine_candidate_batch(
                entries,
                cue_by_id=cue_by_id,
                word_by_id=word_by_id,
                context=context,
                profile_id=profile_id,
                source_language=source_language,
                target_language=target_language,
                is_cancelled=is_cancelled,
                request_state=request_state,
                validation_retry=True,
            )
        raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "语言模型没有返回完整的字幕长度调整结果。")

    replacements: dict[int, list[dict]] = {}
    validation_error: AppException | None = None
    for (index, source, timed), raw_item in zip(entries, raw_items):
        source_cues = _fit_source_cues(source, cue_by_id, word_by_id)
        allowed_research_ids = {
            str(item.get("question_id")) for item in source.get("research_usage") or [] if item.get("question_id")
        }
        try:
            parsed = _validate_fitted_boundary_segments(
                raw_item.get("segments"),
                source=source,
                source_cues=source_cues,
                cue_by_id=cue_by_id,
                word_by_id=word_by_id,
                allowed_research_ids=allowed_research_ids,
            )
        except AppException as exc:
            validation_error = exc
            break
        for item in parsed:
            if not item.get("adaptation_note") and source.get("adaptation_note"):
                item["adaptation_note"] = source["adaptation_note"]
            if not item.get("research_usage") and source.get("research_usage"):
                item["research_usage"] = list(source["research_usage"])
        replacements[index] = parsed
    if validation_error is not None:
        if not validation_retry:
            return _refine_candidate_batch(
                entries,
                cue_by_id=cue_by_id,
                word_by_id=word_by_id,
                context=context,
                profile_id=profile_id,
                source_language=source_language,
                target_language=target_language,
                is_cancelled=is_cancelled,
                request_state=request_state,
                validation_retry=True,
            )
        raise validation_error
    return replacements


def _claim_fit_request(request_state: dict) -> float:
    elapsed = time.perf_counter() - float(request_state["started_at"])
    requests = int(request_state["requests"])
    if requests >= LOCALIZATION_FIT_MAX_REQUESTS or elapsed >= LOCALIZATION_FIT_MAX_SECONDS:
        raise AppException(
            504,
            "VIDEO_LOCALIZATION_FIT_LIMIT_REACHED",
            "字幕长度调整已达到本次任务的处理上限，未覆盖当前本土化字幕轨。请稍后重试。",
            {"request_count": requests, "elapsed_ms": round(elapsed * 1000)},
        )
    request_state["requests"] = requests + 1
    return max(1.0, min(150.0, LOCALIZATION_FIT_MAX_SECONDS - elapsed))


def _validate_fitted_boundary_segments(
    raw_segments,
    *,
    source: dict,
    source_cues: list[VideoLocalizationCue],
    cue_by_id: dict[str, VideoLocalizationCue],
    word_by_id: dict,
    allowed_research_ids: set[str],
) -> list[dict]:
    if not isinstance(raw_segments, list) or not raw_segments:
        raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "语言模型没有返回可用的字幕长度调整结果。")

    ordered_word_ids = [word_id for word_id in source["source_word_ids"] if word_id in word_by_id]
    ordered_cue_ids = [cue_id for cue_id in source["source_cue_ids"] if cue_id in cue_by_id]
    cursor = 0
    mapped_segments: list[dict] = []
    use_word_boundaries = bool(ordered_word_ids) and _fit_uses_word_boundaries(source_cues, word_by_id)
    if use_word_boundaries:
        positions = {word_id: index for index, word_id in enumerate(ordered_word_ids)}
        word_to_cue = {
            word_id: cue.cue_id for cue in source_cues for word_id in cue.source_word_ids if word_id in positions
        }
        boundary_key = "end_word_id"
        final_boundary = ordered_word_ids[-1]
    else:
        positions = {cue_id: index for index, cue_id in enumerate(ordered_cue_ids)}
        word_to_cue = {}
        boundary_key = "end_cue_id"
        final_boundary = ordered_cue_ids[-1] if ordered_cue_ids else ""

    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "字幕长度调整结果包含无效片段。")
        boundary = _text(raw.get(boundary_key), 120)
        boundary_index = positions.get(boundary, -1)
        if boundary_index < cursor:
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_FIT_INVALID",
                "字幕长度调整结果的结束位置缺失、重复或顺序错误。",
            )
        if use_word_boundaries:
            source_word_ids = ordered_word_ids[cursor : boundary_index + 1]
            source_cue_ids = list(dict.fromkeys(word_to_cue.get(word_id) for word_id in source_word_ids))
            source_cue_ids = [cue_id for cue_id in source_cue_ids if cue_id]
        else:
            source_cue_ids = ordered_cue_ids[cursor : boundary_index + 1]
            source_cue_id_set = set(source_cue_ids)
            source_word_ids = [
                word_id
                for cue in source_cues
                if cue.cue_id in source_cue_id_set
                for word_id in cue.source_word_ids
                if word_id in word_by_id
            ]
        mapped_segments.append(
            {
                **raw,
                "source_cue_ids": source_cue_ids,
                "source_word_ids": source_word_ids,
            }
        )
        cursor = boundary_index + 1

    if (
        not final_boundary
        or cursor != len(positions)
        or _text(raw_segments[-1].get(boundary_key), 120) != final_boundary
    ):
        raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "字幕长度调整结果没有连续覆盖到原文末尾。")

    allowed_ids = {cue.cue_id for cue in source_cues}
    allowed_word_ids = {word_id for cue in source_cues for word_id in cue.source_word_ids if word_id in word_by_id}
    return _validate_localized_segments(
        mapped_segments,
        source_cues,
        allowed_ids,
        allowed_word_ids,
        cue_by_id,
        word_by_id,
        allowed_research_ids=allowed_research_ids,
    )


def _fit_uses_word_boundaries(source_cues: list[VideoLocalizationCue], word_by_id: dict) -> bool:
    return bool(source_cues) and all(
        any(word_id in word_by_id for word_id in cue.source_word_ids) for cue in source_cues
    )


def _fit_source_cues(
    item: dict, cue_by_id: dict[str, VideoLocalizationCue], word_by_id: dict
) -> list[VideoLocalizationCue]:
    source_cues = [cue_by_id[cue_id] for cue_id in item["source_cue_ids"] if cue_id in cue_by_id]
    candidate_word_ids = [word_id for word_id in item["source_word_ids"] if word_id in word_by_id]
    if not candidate_word_ids:
        return source_cues

    candidate_word_id_set = set(candidate_word_ids)
    scoped_cues = []
    for cue in source_cues:
        scoped_word_ids = [word_id for word_id in cue.source_word_ids if word_id in candidate_word_id_set]
        patch: dict[str, object] = {"source_word_ids": scoped_word_ids}
        if scoped_word_ids:
            words = [word_by_id[word_id] for word_id in scoped_word_ids]
            patch.update(
                {
                    "start_ms": min(word.start_ms for word in words),
                    "end_ms": max(word.end_ms for word in words),
                    "en_subtitle_text": _join_source_words([word.text for word in words]),
                }
            )
        scoped_cues.append(cue.model_copy(update=patch))
    return scoped_cues


def _split_identical_ranges(items: list[dict]) -> None:
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for item in items:
        groups[(item["start_ms"], item["end_ms"])].append(item)
    for (start_ms, end_ms), group in groups.items():
        if len(group) < 2:
            continue
        total_weight = sum(max(1, _readable_chars(item["display_text"])) for item in group)
        cursor = start_ms
        consumed = 0
        for index, item in enumerate(group):
            consumed += max(1, _readable_chars(item["display_text"]))
            boundary = (
                end_ms if index == len(group) - 1 else start_ms + round((end_ms - start_ms) * consumed / total_weight)
            )
            item["start_ms"] = cursor
            item["end_ms"] = max(cursor + 1, boundary)
            cursor = boundary


def _quality_review(
    timed: list[dict],
    *,
    draft: VideoLocalizationDraft,
    context: dict,
    profile_id: str,
    source_language: str,
    target_language: str,
    is_cancelled: CancelCallback | None,
    on_batch: Callable[[int, int], None],
    on_split: Callable[[int, int], None] | None = None,
    diagnostics: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    started_at = time.perf_counter()
    request_state = {"started_at": started_at, "requests": 0}
    split_count = 0
    batches = _quality_review_batches(timed)
    reviewed: list[dict] = []
    changes: list[dict] = []
    for batch_index, batch in enumerate(batches):
        _ensure_active(is_cancelled)

        def review_batch(items: list[dict]) -> tuple[list[dict], list[dict]]:
            nonlocal split_count
            _ensure_active(is_cancelled)
            timeout = _claim_quality_review_request(request_state)
            try:
                raw = llm_runtime.complete_json(
                    system_prompt=(
                        "你是中国大陆影视本土化终审，要像中文母语口播编辑一样按顺序通读整批字幕。"
                        "逐项核对原文意图、数字、否定、因果、人物口吻、文化转述和字幕可读性；"
                        "还要主动找出英语语序、书面公文腔、词典式直译、中文搭配或补语缺失，以及人和物的代词指代错误。"
                        "把生硬表达改成同一个人面对镜头时会自然说出口的短句，可调整语序并省略中文里多余的主语和将来时，"
                        "但不能删掉事实、论证、情绪强度或关键动作。术语和形容词必须结合当前对象与行业语境判断。"
                        "只修正确有问题的项目；不要把自然口语改回翻译腔，也不要为了口语化添加原文没有的网络热梗。"
                        "不要把人物原有的迟疑、自我修正、接话、质疑或强调统一润色成顺滑播音稿；语气词要按话语功能保留或删除。"
                        "上屏字幕少用常规标点，配音台词保留必要语气和停顿。"
                        "必须检查全部输入，但只回传实际需要修改的项目。只返回 JSON。"
                    ),
                    user_payload={
                        "task": f"{LOCALIZATION_PROMPT_VERSION}:quality-review",
                        "source_language": source_language,
                        "target_language": target_language,
                        "context": context,
                        "review_rules": {
                            "spoken_chinese": "听起来应像同一位说话人即兴讲解，不像译稿、说明书或演讲稿",
                            "sentence_order": (
                                "优先使用中文自然语序和短主动句；逐项检查‘我将、怎样、进行、创建、获得、实现、其、该、从而’"
                                "是否只是英文句法或书面稿残留，有翻译腔就改写，没有问题才保留"
                            ),
                            "collocation": "检查动词与宾语、结果补语、介词和固定搭配是否完整自然，不能漏掉‘出、到、起来、下去’等必要补语",
                            "pronouns": (
                                "按真实指代而不是英文代词字面区分人物的他/她和产品、模型、画面、提示词等非人物的它；"
                                "上下文明显指向 AI 或工具时，即使 ASR 写成 he/she，也要改为它或省略代词"
                            ),
                            "terminology": (
                                "同一个英文词必须按具体对象说成观众能感知的结果；例如 AI 画面语境的 clean 不默认译为‘干净’，"
                                "应按上下文选清晰、自然、没瑕疵或可直接使用，其他行业词也按同一原则处理"
                            ),
                            "speaker_voice": (
                                "保持说话人的正式或随意、专业或外行、笃定或迟疑、节奏与强调方式；"
                                "保留有意义的自我修正、接话、质疑和打断，不要统一改成播音稿"
                            ),
                            "discourse_markers": (
                                "语气词表达犹豫、立场、转折、人物习惯或观众关系时自然保留；属于 ASR 噪声时删除；"
                                "不能为了口语化凭空添加口头禅"
                            ),
                            "cultural_function": (
                                "文化、地点、产品、头衔、笑点、比喻和典故要让中国观众听懂，同时保留原世界与原功能；"
                                "不得替换成无关中国梗或增加原文没有的事实"
                            ),
                            "read_aloud_test": "逐条默读一遍；如果正常中国创作者面对镜头不会这样说，就在不丢信息的前提下改成能直接说出口的表达",
                            "preserve": ["事实", "数字", "专名", "否定", "因果", "比较", "动作", "人物口吻", "情绪强度"],
                        },
                        "display_limits": {
                            "max_duration_ms": MAX_SUBTITLE_DURATION_MS,
                            "max_visible_chars": MAX_CHARS_PER_LINE * 2,
                            "max_chinese_chars_per_second": MAX_CHINESE_CPS,
                        },
                        "items": [
                            {
                                "id": item["id"],
                                "source_text": item["source_text"],
                                "display_text": item["display_text"],
                                "tts_text": item["tts_text"],
                                "duration_ms": item["end_ms"] - item["start_ms"],
                                "review_focus": _localization_review_focus(item, context),
                            }
                            for item in items
                        ],
                        "output": (
                            "返回 checked_ids 和 changes。checked_ids 必须按输入顺序完整列出所有 id；"
                            "review_focus 非空的项目必须逐条完成所列检查；确认有问题时必须放入 changes，确认无问题才可不修改。"
                            "changes 只包含确实需要修改的项目，每项包含 id、display_text、tts_text、reason。"
                            "没有修改时 changes 返回空数组。"
                        ),
                    },
                    profile_id=profile_id,
                    temperature=0.05,
                    max_tokens=6000,
                    timeout=timeout,
                )
            except llm_runtime.LlmRuntimeError as exc:
                if exc.code not in {
                    "llm_output_truncated",
                    "llm_timeout",
                    "llm_response_too_large",
                    "llm_json_invalid",
                    "llm_json_not_object",
                } or len(items) <= 1:
                    raise
                raw = None

            expected_ids = [item["id"] for item in items]
            legacy_items = raw.get("items") if isinstance(raw, dict) else None
            raw_changes = (
                raw
                if isinstance(raw, list)
                else legacy_items
                if isinstance(legacy_items, list)
                else raw.get("changes")
                if isinstance(raw, dict)
                else None
            )
            returned_ids = (
                [str(item.get("id")) for item in raw if isinstance(item, dict)]
                if isinstance(raw, list)
                else [str(item.get("id")) for item in legacy_items if isinstance(item, dict)]
                if isinstance(legacy_items, list)
                else [str(item) for item in raw.get("checked_ids") or []]
                if isinstance(raw, dict)
                else []
            )
            changes_valid = isinstance(raw_changes, list) and all(isinstance(item, dict) for item in raw_changes)
            change_ids = [str(item.get("id")) for item in raw_changes] if changes_valid else []
            changes_valid = changes_valid and len(change_ids) == len(set(change_ids)) and set(change_ids) <= set(expected_ids)
            if returned_ids != expected_ids or not changes_valid:
                if len(items) <= 1:
                    raise AppException(
                        422,
                        "VIDEO_LOCALIZATION_REVIEW_INVALID",
                        "本土化终审没有返回完整结果，任务已停止写入。",
                        {"expected_ids": expected_ids, "returned_ids": returned_ids},
                    )
                if on_split:
                    on_split(batch_index + 1, len(batches))
                split_count += 1
                midpoint = len(items) // 2
                left_reviewed, left_changes = review_batch(items[:midpoint])
                right_reviewed, right_changes = review_batch(items[midpoint:])
                return [*left_reviewed, *right_reviewed], [*left_changes, *right_changes]

            batch_reviewed: list[dict] = []
            batch_changes: list[dict] = []
            decisions_by_id = {str(item["id"]): item for item in raw_changes}
            for source in items:
                decision = decisions_by_id.get(source["id"])
                if decision is None:
                    batch_reviewed.append(source)
                    continue
                display_text = (
                    _normalize_display_text(_text(decision.get("display_text"), 800)) or source["display_text"]
                )
                tts_text = _text(decision.get("tts_text"), 1000) or source["tts_text"]
                if not _numbers_preserved(source["source_text"], display_text) or not _numbers_preserved(
                    source["source_text"], tts_text
                ):
                    display_text, tts_text = source["display_text"], source["tts_text"]
                changed = display_text != source["display_text"] or tts_text != source["tts_text"]
                if changed:
                    batch_changes.append(
                        {
                            "id": source["id"],
                            "before": source["display_text"],
                            "after": display_text,
                            "tts_before": source["tts_text"],
                            "tts_after": tts_text,
                            "reason": _text(decision.get("reason"), 500) or "终审修正了语义或口语表达",
                        }
                    )
                batch_reviewed.append({**source, "display_text": display_text, "tts_text": tts_text})
            return batch_reviewed, batch_changes

        batch_reviewed, batch_changes = review_batch(batch)
        reviewed.extend(batch_reviewed)
        changes.extend(batch_changes)
        on_batch(batch_index + 1, len(batches))
    if diagnostics is not None:
        diagnostics.update(
            {
                "planned_batch_count": len(batches),
                "request_count": int(request_state["requests"]),
                "split_count": split_count,
                "duration_ms": _elapsed_ms(started_at),
            }
        )
    return reviewed, changes


def _quality_review_batches(timed: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for item in timed:
        item_chars = sum(len(str(item.get(key) or "")) for key in ("source_text", "display_text", "tts_text"))
        exceeds_batch = current and (
            len(current) >= QUALITY_REVIEW_BATCH_MAX_ITEMS
            or current_chars + item_chars > QUALITY_REVIEW_BATCH_MAX_TEXT_CHARS
        )
        if exceeds_batch:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        batches.append(current)
    return batches


def _localization_review_focus(item: dict, context: dict) -> list[str]:
    source = str(item.get("source_text") or "")
    source_lower = source.lower()
    display = str(item.get("display_text") or "")
    focus: list[str] = []
    if re.search(r"\b(clean|cleanest|clear|clearest|sharp|sharpest)\b", source_lower) and "干净" in display:
        focus.append("面向普通观众不能单独用‘干净’描述视觉质量；必须结合画面语境改成清晰、自然、少瑕疵或可直接使用等具体结果")
    if re.search(r"\b(he|him|his|she|her|hers)\b", source_lower) and re.search(r"[他她]", display):
        focus.append("英文人称代词可能来自 ASR 或口语指代；结合整批上下文确认实际对象是人物还是 AI、工具、模型或提示词")
    if "不了" in display:
        focus.append("检查‘不了’是否缺少中文必要的结果补语；按真实含义判断应保留‘不了’还是改为‘不出、不到、不开、不了解’等完整搭配")
    if "体现不了" in display:
        focus.append("‘体现不了’在当前观看效果语境搭配生硬，必须按原意改成‘看不出、体现不出、展现不出’一类自然结果表达")
    if any(marker in display for marker in ("我将", "向你展示怎样", "进行创建", "获得效果", "从而实现")):
        focus.append("存在明显书面句式或英文语序，改为同一说话人面对镜头时会直接说出口的短主动句")
    if context.get("speakers") and len(context.get("speakers") or []) == 1:
        if re.search(r"[他她]", display) and any(term in source_lower for term in ("prompt", "model", "tool", "ai", "seedance")):
            focus.append("当前场景只有一位主讲人且句中涉及工具或 AI，重点复核‘他/她’是否应为‘它’或直接省略")
    return focus


def _claim_quality_review_request(request_state: dict) -> float:
    elapsed = time.perf_counter() - float(request_state["started_at"])
    requests = int(request_state["requests"])
    if requests >= QUALITY_REVIEW_MAX_REQUESTS or elapsed >= QUALITY_REVIEW_MAX_SECONDS:
        raise AppException(
            504,
            "VIDEO_LOCALIZATION_REVIEW_LIMIT_REACHED",
            "字幕语义复核已达到本次任务的处理上限，未覆盖当前本土化字幕轨。请稍后重试。",
            {"request_count": requests, "elapsed_ms": round(elapsed * 1000)},
        )
    request_state["requests"] = requests + 1
    return max(1.0, min(180.0, QUALITY_REVIEW_MAX_SECONDS - elapsed))


def _finalize_timing(items: list[dict], draft: VideoLocalizationDraft) -> list[dict]:
    ordered = sorted(items, key=lambda item: (item["start_ms"], item["end_ms"], item["id"]))
    media_end = int(draft.source_media.duration_ms or 0) or None
    for index, item in enumerate(ordered):
        flags = set(item.get("quality_flags") or [])
        next_start = ordered[index + 1]["start_ms"] if index + 1 < len(ordered) else media_end
        if index and item["start_ms"] < ordered[index - 1]["end_ms"]:
            item["start_ms"] = ordered[index - 1]["end_ms"]
        if next_start is not None:
            item["end_ms"] = min(item["end_ms"], next_start)

    _ensure_minimum_subtitle_durations(ordered, media_end=media_end)

    for item in ordered:
        flags = set(item.get("quality_flags") or [])
        duration = max(1, item["end_ms"] - item["start_ms"])
        chars = _readable_chars(item["display_text"])
        cps = chars * 1000 / duration
        if duration < MIN_SUBTITLE_DURATION_MS:
            flags.add("localized_duration_short")
        if duration > MAX_SUBTITLE_DURATION_MS:
            flags.add("localized_duration_long")
        if cps > MAX_CHINESE_CPS:
            flags.add("localized_reading_speed_high")
        if chars > MAX_CHARS_PER_LINE * 2:
            flags.add("localized_text_too_long")
        item["quality_flags"] = sorted(flags)
        item["cps"] = round(cps, 1)
    return ordered


def _ensure_minimum_subtitle_durations(items: list[dict], *, media_end: int | None) -> None:
    original_ranges = [(item["start_ms"], item["end_ms"]) for item in items]
    if _borrow_minimum_duration_from_neighbors(items, media_end=media_end):
        return
    for item, (start_ms, end_ms) in zip(items, original_ranges):
        item["start_ms"] = start_ms
        item["end_ms"] = end_ms
    _schedule_minimum_durations(items, media_end=media_end)


def _borrow_minimum_duration_from_neighbors(items: list[dict], *, media_end: int | None) -> bool:
    for index, item in enumerate(items):
        deficit = MIN_SUBTITLE_DURATION_MS - (item["end_ms"] - item["start_ms"])
        if deficit <= 0:
            continue

        previous = items[index - 1] if index else None
        following = items[index + 1] if index + 1 < len(items) else None

        left_limit = previous["end_ms"] if previous is not None else 0
        take = min(deficit, max(0, item["start_ms"] - left_limit))
        item["start_ms"] -= take
        deficit -= take

        right_limit = following["start_ms"] if following is not None else media_end
        if right_limit is None:
            right_limit = item["end_ms"] + deficit
        take = min(deficit, max(0, right_limit - item["end_ms"]))
        item["end_ms"] += take
        deficit -= take

        if deficit > 0 and previous is not None:
            available = max(0, previous["end_ms"] - previous["start_ms"] - MIN_SUBTITLE_DURATION_MS)
            take = min(deficit, available)
            previous["end_ms"] -= take
            item["start_ms"] = previous["end_ms"]
            deficit -= take

        if deficit > 0 and following is not None:
            available = max(0, following["end_ms"] - following["start_ms"] - MIN_SUBTITLE_DURATION_MS)
            take = min(deficit, available)
            following["start_ms"] += take
            item["end_ms"] = following["start_ms"]
            deficit -= take

        if deficit > 0:
            return False
    return True


def _schedule_minimum_durations(items: list[dict], *, media_end: int | None) -> None:
    scheduled: list[tuple[int, int]] = []
    previous_end = 0
    for item in items:
        preferred_duration = max(MIN_SUBTITLE_DURATION_MS, item["end_ms"] - item["start_ms"])
        start_ms = max(item["start_ms"], previous_end)
        end_ms = start_ms + preferred_duration
        scheduled.append((start_ms, end_ms))
        previous_end = end_ms

    if media_end is not None and scheduled and scheduled[-1][1] > media_end:
        next_start = media_end
        adjusted: list[tuple[int, int]] = [(0, 0)] * len(items)
        for index in range(len(items) - 1, -1, -1):
            start_ms, end_ms = scheduled[index]
            end_ms = min(end_ms, next_start)
            start_ms = min(start_ms, end_ms - MIN_SUBTITLE_DURATION_MS)
            if start_ms < 0:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_TIMING_TOO_DENSE",
                    "本土化字幕时间过于密集，无法保证每条字幕至少显示 833 毫秒。",
                    {"subtitle_id": items[index]["id"], "minimum_duration_ms": MIN_SUBTITLE_DURATION_MS},
                )
            adjusted[index] = (start_ms, end_ms)
            next_start = start_ms
        scheduled = adjusted

    for item, (start_ms, end_ms) in zip(items, scheduled):
        item["start_ms"] = start_ms
        item["end_ms"] = end_ms


def _with_localized_track(
    draft: VideoLocalizationDraft,
    items: list[dict],
    *,
    fingerprint: str,
    context: dict,
    research: dict,
    source_language: str,
    target_language: str,
    profile_id: str,
    model_id: str,
    localization_level: str,
    worldview_permeability: str,
) -> VideoLocalizationDraft:
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=item["id"],
            start_ms=item["start_ms"],
            end_ms=item["end_ms"],
            text=item["display_text"],
            tts_text=item["tts_text"],
            linked_cue_id=item["source_cue_ids"][0] if item["source_cue_ids"] else None,
            source_cue_ids=item["source_cue_ids"],
            source_word_ids=item["source_word_ids"],
            adaptation_note=item["adaptation_note"] or None,
            quality_flags=["generated_localization_draft", *item["quality_flags"]],
        )
        for item in items
    ]
    outputs_by_primary: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if item["source_cue_ids"]:
            outputs_by_primary[item["source_cue_ids"][0]].append(item)
    next_cues = []
    for cue in draft.cues:
        outputs = outputs_by_primary.get(cue.cue_id, [])
        if outputs:
            next_cues.append(
                cue.model_copy(
                    update={
                        "zh_localized_subtitle_text": "\n".join(item["display_text"] for item in outputs),
                        "tts_recommended_text": "\n".join(item["tts_text"] for item in outputs),
                        "quality_flags": sorted(
                            set(
                                [
                                    *cue.quality_flags,
                                    "localization_draft",
                                    f"localization_prompt:{LOCALIZATION_PROMPT_VERSION}",
                                ]
                            )
                        ),
                    }
                )
            )
        else:
            next_cues.append(
                cue.model_copy(
                    update={
                        "zh_localized_subtitle_text": None,
                        "tts_recommended_text": None,
                        "quality_flags": [flag for flag in cue.quality_flags if not flag.startswith("localization")],
                    }
                )
            )
    state = {
        "status": "draft",
        "source_language": source_language,
        "target_language": target_language,
        "source_fingerprint": fingerprint,
        "profile_id": profile_id,
        "model_id": model_id,
        "prompt_version": LOCALIZATION_PROMPT_VERSION,
        "localization_level": localization_level,
        "worldview_permeability": worldview_permeability,
        "context": context,
        "research": research,
        "subtitle_count": len(subtitles),
        "created_at": now_iso(),
    }
    return draft.model_copy(update={"localized_subtitles": subtitles, "cues": next_cues, "localization_state": state})


def _context_step_result(context: dict, draft: VideoLocalizationDraft) -> dict:
    speakers = [
        {
            "title": _text(item.get("speaker_id"), 120) or f"人物 {index}",
            "text": _text(item.get("persona"), 500) or "未发现足够证据形成明确人物画像",
            "facts": [
                {"label": "说话习惯", "value": _text(item.get("speech_habits"), 300) or "按原文口吻处理"},
                {"label": "人物关系", "value": _text(item.get("relationship"), 300) or "原文未明确"},
                {"label": "当前情绪", "value": _text(item.get("emotion"), 200) or "按原文语气判断"},
            ],
        }
        for index, item in enumerate(context.get("speakers") or [], start=1)
    ]
    return _result(
        "success",
        context.get("overview") or "已完成原文、场景与人物口吻分析。",
        [
            ("原文字幕", len(draft.cues)),
            ("识别人物", len(context.get("speakers") or [])),
            ("内容主题", len(context.get("topics") or [])),
        ],
        [("人物与表达方式", speakers)],
        [
            f"时代背景：{context['era']}" if context.get("era") else None,
            f"场景：{context['setting']}" if context.get("setting") else None,
        ],
    )


def _research_step_result(research: dict, localized_items: list[dict] | None = None) -> dict:
    usage_by_question: dict[str, list[dict[str, str]]] = defaultdict(list)
    for localized in localized_items or []:
        for usage in localized.get("research_usage") or []:
            question_id = _text(usage.get("question_id"), 80)
            if not question_id:
                continue
            usage_by_question[question_id].append(
                {
                    "effect": _text(usage.get("effect"), 300),
                    "display_text": _text(localized.get("display_text"), 160),
                }
            )
    items = []
    for index, question in enumerate(research.get("questions") or [], start=1):
        sources = question.get("sources") or []
        usages = usage_by_question.get(str(question.get("question_id") or ""), [])
        effects: list[str] = []
        for usage in usages:
            effect = usage.get("effect")
            if effect and effect not in effects:
                effects.append(effect)
        if usages:
            conclusion = f"这项资料实际影响了 {len(usages)} 段字幕。" + "；".join(effects[:4])
            adoption = f"已用于 {len(usages)} 段字幕"
            tone = "positive"
        elif sources:
            conclusion = "已把资料提供给本土化模型，但模型没有标记任何直接改写；本次仅作为背景参考。"
            adoption = "仅参考，未直接采用"
            tone = "neutral"
        else:
            conclusion = "没有找到足够可靠的公开资料，因此不会据此改写。"
            adoption = "不采用"
            tone = "warning"
        items.append(
            {
                "title": f"问题 {index} · {question.get('query')}",
                "text": conclusion,
                "facts": [
                    {"label": "为什么要查", "value": question.get("reason") or "避免名称或文化背景误译"},
                    {"label": "重点内容", "value": "、".join(question.get("target_terms") or []) or "相关背景"},
                    {"label": "采用结果", "value": adoption},
                ],
                "links": [
                    {
                        "title": source["title"],
                        "url": source["url"],
                        "text": source.get("snippet"),
                        "meta": source.get("provider"),
                    }
                    for source in sources[:12]
                ],
                "tone": tone,
            }
        )
    status = research.get("status") or "not_needed"
    return _result(
        "success"
        if status == "completed"
        else "warning"
        if status in {"partial", "disabled"}
        else "skipped"
        if status == "not_needed"
        else "failed",
        research.get("reason") or "本次没有需要联网查证的问题。",
        [
            ("查证问题", len(research.get("questions") or [])),
            ("参考来源", sum(len(item.get("sources") or []) for item in research.get("questions") or [])),
            ("实际影响字幕", sum(len(usages) for usages in usage_by_question.values())),
        ],
        [("逐项查证结果", items)],
    )


def _localize_step_result(items: list[dict], draft: VideoLocalizationDraft) -> dict:
    samples = [
        {
            "title": f"表达 {index}",
            "before": item["source_text"],
            "after": item["display_text"],
            "before_label": "原文意思",
            "after_label": "上屏字幕",
            "text": f"配音台词：{item['tts_text']}"
            + (f"；处理说明：{item['adaptation_note']}" if item.get("adaptation_note") else ""),
            "tone": "positive",
        }
        for index, item in enumerate(items[:20], start=1)
    ]
    return _result(
        "success",
        f"把 {len(draft.cues)} 条原文字幕按完整语义整理为 {len(items)} 段自然中文表达。",
        [("原文字幕", len(draft.cues)), ("中文语义段", len(items)), ("抽查样例", len(samples))],
        [("原文与中文表达对照", samples)],
    )


def _fit_step_result(before: list[dict], after: list[dict], diagnostics: dict) -> dict:
    rounds = [
        {
            "title": f"第 {item['round']} 轮",
            "text": (
                f"检查 {item['problem_count']} 段超限字幕，合并为 {item['batch_count']} 次大批量请求处理，"
                f"耗时 {_duration_label(item['duration_ms'])}。"
            ),
            "facts": [
                {"label": "待调整", "value": str(item["problem_count"])},
                {"label": "请求批次", "value": str(item["batch_count"])},
            ],
        }
        for item in diagnostics.get("rounds") or []
    ]
    request_count = int(diagnostics.get("request_count") or 0)
    return _result(
        "warning" if diagnostics.get("unresolved_count") else "success",
        (
            f"把初稿中的超长或阅读过快片段调整为 {len(after)} 段可上屏字幕。"
            if request_count
            else "初次中文生成已满足字幕长度与阅读速度要求，无需额外调用模型返修。"
        ),
        [
            ("调整前片段", len(before)),
            ("调整后片段", len(after)),
            ("模型请求", request_count),
            ("调整轮数", int(diagnostics.get("round_count") or 0)),
        ],
        [("逐轮处理记录", rounds)],
    )


def _post_review_constraint_step_result(before: list[dict], after: list[dict], diagnostics: dict) -> dict:
    before_violations = sum(_candidate_exceeds_budget(item) for item in before)
    after_violations = sum(_candidate_exceeds_budget(item) for item in after)
    request_count = int(diagnostics.get("request_count") or 0)
    return _result(
        "warning" if after_violations else "success",
        (
            f"终审后重新检查 {len(before)} 段字幕，返修 {before_violations} 段，全部满足上屏限制。"
            if before_violations
            else f"终审后重新检查 {len(before)} 段字幕，没有产生新的时长、字数或阅读速度问题。"
        ),
        [
            ("重新检查", len(before)),
            ("二次返修", before_violations),
            ("返修后片段", len(after)),
            ("模型请求", request_count),
            ("剩余超限", after_violations),
        ],
    )


def _timing_step_result(items: list[dict]) -> dict:
    samples = [
        {
            "title": item["display_text"],
            "text": f"字幕从 {_timecode(item['start_ms'])} 显示到 {_timecode(item['end_ms'])}。",
            "facts": [
                {"label": "时间依据", "value": item["timing_source"]},
                {"label": "持续时间", "value": _duration_label(item["end_ms"] - item["start_ms"])},
                {"label": "来源范围", "value": f"{len(item['source_cue_ids'])} 条原文字幕"},
            ],
        }
        for item in items[:20]
    ]
    return _result(
        "success",
        "已根据原文词语时间、语义范围和相邻字幕边界安排初步时间，所有片段互不重叠。",
        [
            ("已安排片段", len(items)),
            ("使用逐词时间", sum(item["timing_source"] == "源音频逐词时间" for item in items)),
        ],
        [("时间安排样例", samples)],
    )


def _quality_step_result(items: list[dict], changes: list[dict], diagnostics: dict | None = None) -> dict:
    diagnostics = diagnostics or {}
    risky = [item for item in items if item.get("quality_flags")]
    samples = [
        {
            "title": change["id"],
            "before": change["before"],
            "after": change["after"],
            "before_label": "初稿",
            "after_label": "复核后",
            "text": change["reason"],
            "tone": "positive",
        }
        for change in changes[:50]
    ]
    warnings = [
        {
            "title": item["display_text"],
            "text": _quality_flags_label(item["quality_flags"]),
            "facts": [
                {"label": "显示时长", "value": _duration_label(item["end_ms"] - item["start_ms"])},
                {"label": "阅读速度", "value": f"{item.get('cps', 0)} 字/秒"},
            ],
            "tone": "warning",
        }
        for item in risky[:50]
    ]
    return _result(
        "warning" if risky else "success",
        f"终审检查了 {len(items)} 段字幕，修正 {len(changes)} 段表达；{len(risky)} 段建议人工试听确认。",
        [
            ("复核字幕", len(items)),
            ("表达修正", len(changes)),
            ("建议试听", len(risky)),
            ("计划批次", diagnostics.get("planned_batch_count", 0)),
            ("模型请求", diagnostics.get("request_count", 0)),
            ("失败拆分", diagnostics.get("split_count", 0)),
        ],
        [("终审修正", samples), ("需要重点看的字幕", warnings)],
    )


def _write_step_result(subtitles: list[VideoLocalizationSubtitleCue]) -> dict:
    overlaps = sum(left.end_ms > right.start_ms for left, right in zip(subtitles, subtitles[1:]))
    return _result(
        "success" if subtitles and not overlaps else "warning",
        f"已写入 {len(subtitles)} 条本土化字幕，并同时保存上屏字幕、配音台词和原文来源关系。",
        [
            ("写入字幕", len(subtitles)),
            ("时间重叠", overlaps),
            ("包含配音台词", sum(bool(item.tts_text) for item in subtitles)),
        ],
    )


def _result(status: str, summary: str, metrics=(), sections=(), notes=()) -> dict:
    return {
        "status": status,
        "summary": summary,
        "metrics": [{"label": label, "value": str(value)} for label, value in metrics if value is not None],
        "sections": [{"title": title, "items": items} for title, items in sections if items],
        "notes": [note for note in notes if note],
    }


def _cue_payload(cue: VideoLocalizationCue, word_by_id: dict) -> dict:
    duration_ms = max(1, int(cue.end_ms or 0) - int(cue.start_ms or 0))
    return {
        "cue_id": cue.cue_id,
        "speaker_id": cue.speaker_id,
        "start_ms": cue.start_ms,
        "end_ms": cue.end_ms,
        "duration_ms": duration_ms,
        "display_budget": {
            "max_visible_chars_for_this_duration": max(
                1,
                min(MAX_CHARS_PER_LINE * 2, math.floor(duration_ms * MAX_CHINESE_CPS / 1000)),
            ),
            "must_split_if_duration_exceeds_ms": MAX_SUBTITLE_DURATION_MS,
        },
        "text": cue.en_subtitle_text,
        "words": [
            {
                "word_id": word_id,
                "text": word_by_id[word_id].text,
                "start_ms": word_by_id[word_id].start_ms,
                "end_ms": word_by_id[word_id].end_ms,
            }
            for word_id in cue.source_word_ids
            if word_id in word_by_id
        ],
    }


def _pause_boundary_payload(cues: list[VideoLocalizationCue], word_by_id: dict) -> list[dict]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for cue in cues:
        for word_id in cue.source_word_ids:
            if word_id in seen or word_id not in word_by_id:
                continue
            seen.add(word_id)
            ordered.append((cue.cue_id, word_id))

    boundaries: list[dict] = []
    for (left_cue_id, left_id), (right_cue_id, right_id) in zip(ordered, ordered[1:]):
        left = word_by_id[left_id]
        right = word_by_id[right_id]
        gap_ms = max(0, int(right.start_ms) - int(left.end_ms))
        if gap_ms < PREFERRED_PAUSE_SPLIT_MIN_GAP_MS:
            continue
        boundaries.append(
            {
                "after_word_id": left_id,
                "before_word_id": right_id,
                "left_cue_id": left_cue_id,
                "right_cue_id": right_cue_id,
                "gap_ms": gap_ms,
                "strength": "strong" if gap_ms >= 500 else "clear",
                "instruction": "仅当边界两侧都能形成自然中文语义单位时优先在此拆分",
            }
        )
    return boundaries


def _research_payload(research: dict) -> list[dict]:
    return [
        {
            "question_id": item.get("question_id"),
            "question": item.get("query"),
            "reason": item.get("reason"),
            "sources": [
                {"title": source.get("title"), "snippet": source.get("snippet"), "url": source.get("url")}
                for source in item.get("sources") or []
            ],
        }
        for item in research.get("questions") or []
    ]


def _rough_preview(items: list[dict], draft: VideoLocalizationDraft) -> list[dict]:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    result = []
    for index, item in enumerate(items, start=1):
        cues = [cue_by_id[cue_id] for cue_id in item["source_cue_ids"] if cue_id in cue_by_id]
        result.append(
            {
                "subtitle_id": f"localized_{index:04d}",
                "start_ms": min(cue.start_ms or 0 for cue in cues),
                "end_ms": max(cue.end_ms or 1 for cue in cues),
                "text": item["display_text"],
                "tts_text": item["tts_text"],
            }
        )
    return result


def _preview_item(item: dict) -> dict:
    return {
        "subtitle_id": item["id"],
        "start_ms": item["start_ms"],
        "end_ms": item["end_ms"],
        "text": item["display_text"],
        "tts_text": item["tts_text"],
        "quality_flags": item.get("quality_flags") or [],
    }


def _normalize_display_text(value: str) -> str:
    characters = []
    for index, character in enumerate(value):
        if character not in DISPLAY_PUNCTUATION:
            characters.append(character)
            continue
        previous = value[index - 1] if index else ""
        following = value[index + 1] if index + 1 < len(value) else ""
        if character in ",.:" and previous.isdigit() and following.isdigit():
            characters.append(character)
        else:
            characters.append(" ")
    text = "".join(characters)
    return " ".join(text.split()).strip()


def _numbers_preserved(source: str, localized: str) -> bool:
    return _normalized_numbers(source) == _normalized_numbers(localized)


def _normalized_numbers(value: str) -> Counter[str]:
    return Counter(token.replace(",", "").replace("％", "%").upper() for token in NUMBER_PATTERN.findall(value))


def _readable_chars(value: str) -> int:
    return len(re.sub(r"\s|[^\w\u3400-\u9fff]", "", value))


def _quality_flags_label(flags: list[str]) -> str:
    labels = {
        "localized_duration_short": "显示时间偏短",
        "localized_duration_long": "单条持续时间偏长",
        "localized_reading_speed_high": "单位时间字数偏多",
        "localized_text_too_long": "上屏文字偏长",
    }
    return "；".join(labels.get(flag, flag) for flag in flags)


def _timecode(value_ms: int) -> str:
    total_seconds, milliseconds = divmod(max(0, int(value_ms)), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _duration_label(value_ms: int) -> str:
    seconds = max(0, value_ms) / 1000
    return f"{seconds:.2f}".rstrip("0").rstrip(".") + " 秒"


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _string_list(value, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for raw in value[:limit] if (item := _text(raw, item_limit))]


def _dict_list(value, limit: int) -> list[dict]:
    return [item for item in (value if isinstance(value, list) else [])[:limit] if isinstance(item, dict)]


def _report(callback: ProgressCallback | None, progress: float, stage: str) -> None:
    if callback:
        callback(progress, stage)


def _ensure_active(callback: CancelCallback | None) -> None:
    if callback and callback():
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "本土化字幕任务已取消。")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
