from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    now_iso,
)
from app.errors import AppException
from app.services import llm_runtime, settings_store, web_search


LOCALIZATION_PROMPT_VERSION = "localization-draft-v10"
LOCALIZATION_BATCH_MAX_CUES = 512
LOCALIZATION_BATCH_MAX_WORDS = 4_000
LOCALIZATION_BATCH_MAX_SOURCE_CHARS = 24_000
LOCALIZATION_MAX_PARALLEL_BATCHES = 2
QUALITY_REVIEW_BATCH_MAX_ITEMS = 300
QUALITY_REVIEW_BATCH_MAX_TEXT_CHARS = 60_000
QUALITY_REVIEW_MAX_REQUESTS = 8
QUALITY_REVIEW_MAX_SECONDS = 600
TIMED_REVIEW_BATCH_MAX_ITEMS = 180
TIMED_REVIEW_MAX_BATCHES = 1
TIMED_REVIEW_MAX_PARALLEL_BATCHES = 1
TIMED_REVIEW_MAX_CHANGES_PER_BATCH = 24
LOCALIZATION_FIT_BATCH_MAX_ITEMS = 64
LOCALIZATION_FIT_MAX_ROUNDS = 3
LOCALIZATION_FIT_MAX_REQUESTS = 16
LOCALIZATION_FIT_MAX_SECONDS = 600
LOCALIZATION_FIT_MAX_PARALLEL_BATCHES = 2
MIN_SUBTITLE_DURATION_MS = 833
MAX_SUBTITLE_DURATION_MS = 8000
MAX_CHARS_PER_LINE = 16
MAX_CHINESE_CPS = 9.5
HARD_MAX_CHINESE_CPS = 12.1
END_HOLD_MS = 220
PREFERRED_PAUSE_SPLIT_MIN_GAP_MS = 280
PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS = 18
LOCALIZATION_BUNDLE_TARGET_WORDS = 72
LOCALIZATION_BUNDLE_MAX_WORDS = 110
LOCALIZATION_BUNDLE_MIN_WORDS_AT_TRANSITION = 42
LOCALIZATION_TARGET_CUE_VISIBLE_CHARS = 20
CONTEXT_ANALYSIS_MAX_SOURCE_CHARS = 18_000
LOCALIZATION_DOCUMENT_MAX_SOURCE_CHARS = 24_000
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
    processing_draft = _draft_with_repaired_alignment_timing(draft)
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

    _report(on_progress, 0.30, "正在通读全文并生成中文口语")
    semantic_bundles = _semantic_localization_bundles(processing_draft)
    localized_bundles, localization_diagnostics = _localize_semantic_bundles(
        semantic_bundles,
        draft=draft,
        context=context,
        research=research,
        profile_id=profile.profile_id,
        source_language=source_language,
        target_language=target_language,
        localization_level=localization_level,
        worldview_permeability=worldview_permeability,
        is_cancelled=is_cancelled,
    )
    _report(on_progress, 0.64, "正在检查中文表达")
    reviewed_bundles, review_changes, review_diagnostics = _review_localized_bundles(
        localized_bundles,
        profile_id=profile.profile_id,
        is_cancelled=is_cancelled,
    )
    _ensure_active(is_cancelled)
    candidates = _localized_bundles_to_candidates(reviewed_bundles, processing_draft)
    _report(on_progress, 0.76, "正在匹配字幕分段与时间")
    timed = _finalize_timing(_time_candidates(candidates, processing_draft), processing_draft)
    _extend_timed_for_readability(timed, processing_draft)
    timed = _finalize_timing(timed, processing_draft)
    timed_before_fit = [{**item, "quality_flags": list(item.get("quality_flags") or [])} for item in timed]
    problem_entries = [
        (index, item, item)
        for index, item in enumerate(timed)
        if _candidate_exceeds_budget(item)
    ]
    fit_diagnostics: dict = {
        "request_count": 0,
        "round_count": 0,
        "rounds": [],
        "duration_ms": 0,
        "unresolved_count": len(problem_entries),
        "local_adjustment_count": 0,
    }
    compressible_entries = [
        entry for entry in problem_entries if _candidate_only_needs_compression(entry[2])
    ]
    if compressible_entries:
        _report(on_progress, 0.82, f"正在本地精简过快字幕 · {len(compressible_entries)} 段")
        fit_started_at = time.perf_counter()
        replacements = {
            index: [replacement]
            for index, source, item in compressible_entries
            if (replacement := _compress_candidate_locally(source, item)) is not None
        }
        timed = _apply_fit_replacements(timed, replacements)
        fit_duration_ms = _elapsed_ms(fit_started_at)
        fit_diagnostics = {
            "request_count": 0,
            "round_count": 1,
            "rounds": [
                {
                    "round": 1,
                    "problem_count": len(compressible_entries),
                    "batch_count": 1,
                    "duration_ms": fit_duration_ms,
                }
            ],
            "duration_ms": fit_duration_ms,
            "unresolved_count": sum(_candidate_exceeds_budget(item) for item in timed),
            "local_adjustment_count": len(replacements),
        }
    timed_before_review = [{**item, "quality_flags": list(item.get("quality_flags") or [])} for item in timed]
    _report(on_progress, 0.88, "正在对照原文复核字幕时间线")
    timed, timed_review_changes, timed_review_diagnostics = _review_timed_localization(
        timed,
        profile_id=profile.profile_id,
        is_cancelled=is_cancelled,
    )
    timed = _finalize_timing(timed, processing_draft)
    _ensure_localized_timeline_constraints(timed)
    review_changes.extend(timed_review_changes)
    review_diagnostics = {
        **review_diagnostics,
        "planned_batch_count": int(review_diagnostics.get("planned_batch_count") or 0)
        + int(timed_review_diagnostics.get("planned_batch_count") or 0),
        "request_count": int(review_diagnostics.get("request_count") or 0)
        + int(timed_review_diagnostics.get("request_count") or 0),
        "timed_review_duration_ms": timed_review_diagnostics.get("duration_ms", 0),
        "timed_review_mode": timed_review_diagnostics.get("timed_review_mode"),
        "timed_review_fallback_count": timed_review_diagnostics.get("fallback_count", 0),
    }
    step_results["research"] = _research_step_result(research, candidates)
    step_results["localize"] = _localize_step_result(candidates, draft)
    step_results["fit_segments"] = _fit_step_result(timed_before_fit, timed, fit_diagnostics)
    step_results["segment_timing"] = _timing_step_result(timed)
    step_results["quality_review"] = _quality_step_result(timed, review_changes, review_diagnostics)
    step_results["post_review_constraints"] = _post_review_constraint_step_result(
        timed_before_review,
        timed,
        {"review_change_count": len(timed_review_changes)},
    )
    if on_preview:
        on_preview("localized_review", [_preview_item(item) for item in timed])

    _report(on_progress, 0.94, "正在写入本土化字幕轨")
    _ensure_active(is_cancelled)
    next_draft = _with_localized_track(
        draft,
        timed,
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
            "preview_cues": [_preview_item(item) for item in timed],
        },
    )


def with_chinese_draft(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    """Compatibility entry point for the legacy synchronous endpoint."""
    return generate_localization_draft(draft).draft


def _draft_with_repaired_alignment_timing(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    if not draft.transcription or not draft.transcription.words or not draft.transcription.segments:
        return draft
    from app.domains.video_localization import transcription

    repaired_words = transcription._repair_collapsed_word_runs(
        draft.transcription.words,
        segments=draft.transcription.segments,
    )
    if repaired_words == draft.transcription.words:
        return draft
    return draft.model_copy(
        update={
            "transcription": draft.transcription.model_copy(update={"words": repaired_words}),
        }
    )


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
    transcript, transcript_sampling = _context_transcript_sample(draft)
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
        "transcript": transcript,
        "transcript_sampling": transcript_sampling,
        "output": (
            "返回 overview、era、setting、topics、speakers、style_rules、needs_research、research_questions。"
            "speakers 每项包含 speaker_id、persona、speech_habits、relationship、emotion。"
            "research_questions 每项包含 query、reason、category、target_terms；只有外部资料能减少误译时才提出。"
        ),
    }
    system_prompt = (
        "你是影视本土化总编。先理解内容、时代、场景、人物关系、说话习惯与情绪，再决定哪些事实或文化背景需要外部查证。"
        "人物口吻来自原文证据，不得凭空编造。把本土化强度与世界观渗透程度分开考虑。只返回约定 JSON。"
    )
    raw = None
    for attempt in range(2):
        try:
            raw = llm_runtime.complete_json(
                system_prompt=(
                    system_prompt
                    if attempt == 0
                    else system_prompt + "上次响应无法解析；这次必须返回一个紧凑、完整、有效的 JSON 对象，不要输出说明文字。"
                ),
                user_payload=payload,
                profile_id=profile_id,
                temperature=0.1,
                max_tokens=5000,
                timeout=120,
            )
            break
        except llm_runtime.LlmRuntimeError as exc:
            if attempt or exc.code not in {
                "llm_json_invalid",
                "llm_json_not_object",
                "llm_output_truncated",
                "llm_response_invalid",
            }:
                raise
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
        "analysis_input": transcript_sampling,
    }


def _context_transcript_sample(draft: VideoLocalizationDraft) -> tuple[list[dict], dict]:
    rows = [
        {"cue_id": cue.cue_id, "speaker_id": cue.speaker_id, "text": (cue.en_subtitle_text or "").strip()}
        for cue in draft.cues
        if (cue.en_subtitle_text or "").strip()
    ]
    source_chars = sum(len(row["text"]) for row in rows)
    if source_chars <= CONTEXT_ANALYSIS_MAX_SOURCE_CHARS:
        return rows, {
            "mode": "full",
            "source_cue_count": len(rows),
            "included_cue_count": len(rows),
            "source_chars": source_chars,
            "included_chars": source_chars,
        }

    # Context analysis needs global coverage, not precise translation. Fill the
    # budget in round-robin order across beginning, middle, and ending thirds.
    thirds = [
        list(range(0, len(rows) // 3)),
        list(range(len(rows) // 3, 2 * len(rows) // 3)),
        list(range(2 * len(rows) // 3, len(rows))),
    ]
    positions = [0, 0, 0]
    selected: set[int] = set()
    included_chars = 0
    while True:
        advanced = False
        for group_index, indexes in enumerate(thirds):
            position = positions[group_index]
            if position >= len(indexes):
                continue
            index = indexes[position]
            positions[group_index] += 1
            advanced = True
            row_chars = len(rows[index]["text"])
            if row_chars > CONTEXT_ANALYSIS_MAX_SOURCE_CHARS and not selected:
                clipped = {**rows[index], "text": rows[index]["text"][:CONTEXT_ANALYSIS_MAX_SOURCE_CHARS]}
                return [clipped], {
                    "mode": "distributed",
                    "source_cue_count": len(rows),
                    "included_cue_count": 1,
                    "source_chars": source_chars,
                    "included_chars": len(clipped["text"]),
                }
            if included_chars + row_chars > CONTEXT_ANALYSIS_MAX_SOURCE_CHARS:
                continue
            selected.add(index)
            included_chars += row_chars
        if not advanced or included_chars >= CONTEXT_ANALYSIS_MAX_SOURCE_CHARS:
            break
    sampled = [rows[index] for index in sorted(selected)]
    return sampled, {
        "mode": "distributed",
        "source_cue_count": len(rows),
        "included_cue_count": len(sampled),
        "source_chars": source_chars,
        "included_chars": included_chars,
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


def _semantic_localization_bundles(draft: VideoLocalizationDraft) -> list[dict]:
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    cue_order = {cue.cue_id: index for index, cue in enumerate(draft.cues)}
    cue_by_word: dict[str, list[str]] = defaultdict(list)
    for cue in draft.cues:
        for word_id in cue.source_word_ids:
            cue_by_word[word_id].append(cue.cue_id)

    units: list[dict] = []
    if draft.transcription and draft.transcription.segments:
        words_by_segment: dict[str, list] = defaultdict(list)
        for word in draft.transcription.words:
            words_by_segment[word.segment_id].append(word)
        for segment in draft.transcription.segments:
            text = (segment.corrected_text or segment.raw_text or "").strip()
            words = words_by_segment.get(segment.segment_id) or []
            if not text or not words:
                continue
            word_ids = [word.word_id for word in words]
            cue_ids = sorted(
                {cue_id for word_id in word_ids for cue_id in cue_by_word.get(word_id, [])},
                key=lambda cue_id: cue_order.get(cue_id, len(cue_order)),
            )
            units.append(
                {
                    "id": segment.segment_id,
                    "source": text,
                    "source_word_ids": word_ids,
                    "source_cue_ids": cue_ids,
                    "speaker_id": _unit_speaker(cue_ids, draft),
                    "word_count": max(1, len(re.findall(r"\b[\w'-]+\b", text))),
                }
            )
        covered_cue_ids = {cue_id for unit in units for cue_id in unit["source_cue_ids"]}
        for cue in draft.cues:
            text = (cue.en_subtitle_text or "").strip()
            if not text or cue.cue_id in covered_cue_ids:
                continue
            units.append(
                {
                    "id": f"cue_fallback:{cue.cue_id}",
                    "source": text,
                    "source_word_ids": [word_id for word_id in cue.source_word_ids if word_id in word_by_id],
                    "source_cue_ids": [cue.cue_id],
                    "speaker_id": cue.speaker_id,
                    "word_count": max(1, len(re.findall(r"\b[\w'-]+\b", text))),
                }
            )
        units.sort(
            key=lambda unit: min(
                (cue_order.get(cue_id, len(cue_order)) for cue_id in unit["source_cue_ids"]),
                default=len(cue_order),
            )
        )
    if not units:
        for cue in draft.cues:
            text = (cue.en_subtitle_text or "").strip()
            if not text:
                continue
            units.append(
                {
                    "id": cue.cue_id,
                    "source": text,
                    "source_word_ids": [word_id for word_id in cue.source_word_ids if word_id in word_by_id],
                    "source_cue_ids": [cue.cue_id],
                    "speaker_id": cue.speaker_id,
                    "word_count": max(1, len(re.findall(r"\b[\w'-]+\b", text))),
                }
            )

    groups: list[list[dict]] = []
    current: list[dict] = []
    current_words = 0
    for unit in units:
        speaker_changed = bool(
            current
            and current[-1].get("speaker_id")
            and unit.get("speaker_id")
            and current[-1]["speaker_id"] != unit["speaker_id"]
        )
        transition = _starts_localization_transition(unit["source"])
        protected_join = bool(
            current
            and not speaker_changed
            and _must_join_source_units(current[-1]["source"], unit["source"])
        )
        should_flush = bool(
            current
            and not protected_join
            and (
                speaker_changed
                or current_words + unit["word_count"] > LOCALIZATION_BUNDLE_MAX_WORDS
                or (
                    current_words >= LOCALIZATION_BUNDLE_MIN_WORDS_AT_TRANSITION
                    and transition
                )
                or current_words >= LOCALIZATION_BUNDLE_TARGET_WORDS
                and current[-1]["source"].rstrip().endswith((".", "?", "!"))
            )
        )
        if should_flush:
            groups.append(current)
            current = []
            current_words = 0
        current.append(unit)
        current_words += unit["word_count"]
    if current:
        groups.append(current)

    bundles = []
    for index, group in enumerate(groups, start=1):
        bundles.append(
            {
                "id": f"bundle_{index:03d}",
                "speaker_id": next((item["speaker_id"] for item in group if item.get("speaker_id")), None),
                "source": _join_semantic_sources([item["source"] for item in group]),
                "source_unit_ids": [item["id"] for item in group],
                "source_word_ids": [word_id for item in group for word_id in item["source_word_ids"]],
                "source_cue_ids": list(dict.fromkeys(cue_id for item in group for cue_id in item["source_cue_ids"])),
                "units": group,
            }
        )
    _validate_bundle_source_coverage(bundles, draft)
    return bundles


def _validate_bundle_source_coverage(bundles: list[dict], draft: VideoLocalizationDraft) -> None:
    expected_cue_ids = [cue.cue_id for cue in draft.cues if (cue.en_subtitle_text or "").strip()]
    returned_cue_ids = {cue_id for bundle in bundles for cue_id in bundle["source_cue_ids"]}
    missing_cue_ids = [cue_id for cue_id in expected_cue_ids if cue_id not in returned_cue_ids]

    known_word_ids = {word.word_id for word in (draft.transcription.words if draft.transcription else [])}
    expected_word_ids = [
        word_id
        for cue in draft.cues
        if (cue.en_subtitle_text or "").strip()
        for word_id in cue.source_word_ids
        if word_id in known_word_ids
    ]
    returned_word_ids = [word_id for bundle in bundles for word_id in bundle["source_word_ids"]]
    if missing_cue_ids or (expected_word_ids and returned_word_ids != expected_word_ids):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_COVERAGE_INCOMPLETE",
            "本土化输入遗漏、重复或打乱了部分原文，任务已停止写入。",
            {
                "missing_cue_ids": missing_cue_ids[:20],
                "expected_word_count": len(expected_word_ids),
                "returned_word_count": len(returned_word_ids),
            },
        )


def _unit_speaker(cue_ids: list[str], draft: VideoLocalizationDraft) -> str | None:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    speakers = [cue_by_id[cue_id].speaker_id for cue_id in cue_ids if cue_id in cue_by_id and cue_by_id[cue_id].speaker_id]
    return Counter(speakers).most_common(1)[0][0] if speakers else None


def _starts_localization_transition(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:level\s+(?:one|two|three)|now\b|okay\b|all right\b|let me show\b|this time\b|so far\b|and if\b)",
            text.strip(),
            flags=re.IGNORECASE,
        )
    )


def _must_join_source_units(left: str, right: str) -> bool:
    left = left.rstrip()
    right = right.lstrip()
    if re.search(r"\d\.$", left) and re.match(r"\d(?:\b|\s)", right):
        return True
    if not left.endswith((".", "?", "!", ":", ";")):
        return True
    return False


def _join_semantic_sources(texts: list[str]) -> str:
    result = " ".join(text.strip() for text in texts if text.strip())
    return re.sub(r"(?<=\d)\.\s+(?=\d\b)", ".", result).strip()


def _localize_semantic_bundles(
    bundles: list[dict],
    *,
    draft: VideoLocalizationDraft,
    context: dict,
    research: dict,
    profile_id: str,
    source_language: str,
    target_language: str,
    localization_level: str,
    worldview_permeability: str,
    is_cancelled: CancelCallback | None,
    _document_scope: dict | None = None,
    _allow_partition: bool = True,
) -> tuple[list[dict], dict]:
    _ensure_active(is_cancelled)
    source_chars = sum(len(item.get("source") or "") for item in bundles)
    if _allow_partition and source_chars > LOCALIZATION_DOCUMENT_MAX_SOURCE_CHARS:
        started_at = time.perf_counter()
        chapters = _partition_localization_document(bundles)
        localized: list[dict] = []
        diagnostics: list[dict] = []
        for index, chapter in enumerate(chapters):
            previous = chapters[index - 1][-1] if index else None
            following = chapters[index + 1][0] if index + 1 < len(chapters) else None
            chapter_rows, chapter_diagnostics = _localize_semantic_bundles(
                chapter,
                draft=draft,
                context=context,
                research=research,
                profile_id=profile_id,
                source_language=source_language,
                target_language=target_language,
                localization_level=localization_level,
                worldview_permeability=worldview_permeability,
                is_cancelled=is_cancelled,
                _document_scope={
                    "chapter": index + 1,
                    "chapter_count": len(chapters),
                    "previous_anchor": (
                        {"id": previous["id"], "source": previous["source"][-800:]} if previous else None
                    ),
                    "next_anchor": (
                        {"id": following["id"], "source": following["source"][:800]} if following else None
                    ),
                },
                _allow_partition=False,
            )
            localized.extend(chapter_rows)
            diagnostics.append(chapter_diagnostics)
        return localized, {
            "planned_batch_count": len(chapters),
            "partition_count": len(chapters),
            "request_count": sum(int(item.get("request_count") or 0) for item in diagnostics),
            "source_bundle_count": len(bundles),
            "source_chars": source_chars,
            "payload_bytes": sum(int(item.get("payload_bytes") or 0) for item in diagnostics),
            "duration_ms": _elapsed_ms(started_at),
        }
    request_items = [
        {"id": item["id"], "speaker_id": item.get("speaker_id"), "source": item["source"]}
        for item in bundles
    ]
    payload = {
        "task": f"{LOCALIZATION_PROMPT_VERSION}:localize",
        "source_language": source_language,
        "target_language": target_language,
        "profile": {
            "localization_level": localization_level,
            "worldview_permeability": worldview_permeability,
            "audience": "熟悉短视频和 AI 创作工具的中国大陆普通观众",
        },
        "context": context,
        "research": _research_payload(research),
        "glossary": [item.model_dump(mode="json") for item in draft.glossary],
        "document": request_items,
        "document_scope": _document_scope,
        "editorial_rules": [
            "必须先通读全部输入，按整篇中文口播来写，再用输入 id 切回连续语义块；输入块不是逐句翻译单位",
            "保留事实、数字、品牌、否定、因果、比较、人物意图和情绪强度，不得增加原文没有的事实或梗",
            "删掉中文不需要反复出现的主语、将来时和名词化结构，把英语长从句改成自然的中文短句",
            "使用中国大陆创作者真实的流程说法，例如上传素材、把素材交给工具、输入提示词、直接出结果或成片；必须按上下文选择，不能机械套词",
            "clean 要按对象写成清晰、自然、瑕疵少、完成度高或能直接用，不能机械写成干净；do it justice 写成看不出真实效果或体现不出效果",
            "drop into 不一律写丢进，squeeze the most out of 不写榨干，insane 或 crazy 不要每次都翻成疯狂",
            (
                "遇到 walk、move、movement、motion、tracking、framing、rig 等动作或镜头词，必须先结合主语、宾语和前后文判断类型："
                "人物的 walk/gait 是走路动作或步态，物体 motion 是物体运动，camera move 是运镜或镜头移动，handheld move 是手持运镜，"
                "tracking 是跟踪/跟拍/运动跟踪，framing 是构图或取景；只有原文确实强调移动路径时才写运动轨迹。"
                "不能把 my walk 写成‘我的走路’，也不能把 camera/handheld move 笼统写成‘摄像机运动’或‘手持运动’"
            ),
            (
                "源文来自 ASR，连续名词之间可能缺少逗号；必须结合上下文恢复并列项和修饰关系，不能把本来并列的拍摄装置、机位/构图、"
                "人物或车辆动作硬拼成多层定语。还要保留动作与空间变化的真实关系：某段运镜可以被搬到新的场景或高度，运镜本身不会‘变成几千英尺高’"
            ),
            "保留原作者的惊叹、转折、教程推进和自我修正，但不要堆砌其实、你知道吧、对吧，也不要润色成播音稿",
            "每个语义块内部可以重排、合并或拆开句子，只要全文顺序、块级事实覆盖和 id 不变",
            "只返回一份可直接配音的中文 text；使用自然标点标出中文呼吸和语义边界，代码随后负责字幕时间",
        ],
        "output": "优先返回包含 items 数组的对象；若只能输出顶层数组，也可直接返回 items。每项仅含与输入一致的 id、text，可选 note 和 research；数量、顺序、id 必须完全一致。",
    }
    started_at = time.perf_counter()
    system_prompt = (
        "你是中国大陆科技视频的口播总编。任务不是逐句翻译，而是先理解整篇论述、人物口吻、前后照应和行业语境，"
        "再让同一个创作者像原本就用中文录制这期视频一样自然表达。输出前在内部完成全文理解、中文重写和事实核对，"
        "但只返回约定 JSON。"
    )
    raw = None
    rows = None
    expected_ids = [item["id"] for item in bundles]
    returned_ids: list[str] = []
    blank_ids: list[str] = []
    request_count = 0
    for attempt in range(2):
        request_count += 1
        try:
            raw = llm_runtime.complete_json(
                system_prompt=(
                    system_prompt
                    if attempt == 0
                    else system_prompt
                    + "上次结构化响应损坏；这次逐项核对 id 后返回紧凑、完整的 JSON，不要省略、截断或输出解释。"
                ),
                user_payload=payload,
                profile_id=profile_id,
                temperature=0.22 if attempt == 0 else 0.1,
                max_tokens=18_000,
                timeout=300,
                allow_array=True,
            )
        except llm_runtime.LlmRuntimeError as exc:
            if attempt or exc.code not in {
                "llm_json_invalid",
                "llm_json_not_object",
                "llm_output_truncated",
                "llm_response_invalid",
                "llm_response_too_large",
            }:
                raise
            continue
        rows = raw.get("items") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
        returned_ids = [str(item.get("id")) for item in rows or [] if isinstance(item, dict)]
        blank_ids = [
            str(item.get("id"))
            for item in rows or []
            if isinstance(item, dict) and not _text(item.get("text"), 8000)
        ]
        if isinstance(rows, list) and returned_ids == expected_ids and not blank_ids:
            break
    if not isinstance(rows, list) or returned_ids != expected_ids:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_LOCALIZATION_INVALID",
            "整篇本土化没有返回完整结果，任务已停止写入。",
            {"expected_count": len(expected_ids), "returned_count": len(returned_ids)},
        )
    if blank_ids:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_LOCALIZATION_INVALID",
            "整篇本土化包含空白中文内容。",
            {"blank_ids": blank_ids},
        )
    bundle_by_id = {item["id"]: item for item in bundles}
    localized = []
    for row in rows:
        source = bundle_by_id[str(row["id"])]
        text = _text(row.get("text"), 8000)
        localized.append(
            {
                **source,
                "text": text,
                "adaptation_note": _text(row.get("note"), 120),
                "research_usage": _compact_research_usage(row.get("research")),
            }
        )
    return localized, {
        "planned_batch_count": 1,
        "request_count": request_count,
        "source_bundle_count": len(bundles),
        "source_chars": source_chars,
        "partition_count": 1,
        "payload_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        "duration_ms": _elapsed_ms(started_at),
    }


def _partition_localization_document(bundles: list[dict]) -> list[list[dict]]:
    chapters: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for bundle in bundles:
        bundle_chars = len(bundle.get("source") or "")
        if current and current_chars + bundle_chars > LOCALIZATION_DOCUMENT_MAX_SOURCE_CHARS:
            chapters.append(current)
            current = []
            current_chars = 0
        current.append(bundle)
        current_chars += bundle_chars
    if current:
        chapters.append(current)
    return chapters


def _review_localized_bundles(
    bundles: list[dict],
    *,
    profile_id: str,
    is_cancelled: CancelCallback | None,
) -> tuple[list[dict], list[dict], dict]:
    _ensure_active(is_cancelled)
    payload_items = [
        {
            "id": item["id"],
            "source": item["source"],
            "chinese": item["text"],
            "required_numbers": list(_normalized_numbers(item["source"]).elements()),
            "must_repair_numbers": not _numbers_preserved(item["source"], item["text"]),
            "review_focus": _localized_bundle_review_focus(item["source"], item["text"]),
        }
        for item in bundles
    ]
    payload = {
        "task": f"{LOCALIZATION_PROMPT_VERSION}:quality-review",
        "document": payload_items,
        "rules": [
            "must_repair_numbers 为 true 的块必须进入 changes，并在 replacement 中保留 required_numbers 的每个数字、百分比、版本号和规格",
            "review_focus 非空的块必须逐项检查；确认点名的表达有问题时必须进入 changes，不能原样保留被点名的翻译腔",
            "只找确实仍像翻译稿、中文搭配错误、行业流程说法不自然、事实数字不一致或前后指代不连贯的块",
            "changes 只列必须修改的块；不要为了显得在工作而反复润色已经自然的中文",
            "replacement 必须是该块完整替换文本，保留事实、数字、品牌、人物口吻和块级信息覆盖",
            "重点检查英文字面映射、书面腔、工具操作动词、行业术语、中文固定搭配和全文一致性",
            (
                "动作与镜头词必须按语义角色复核：区分人物动作/步态、物体运动、车辆行驶、镜头移动/运镜、手持运镜、"
                "跟踪/跟拍、构图/取景和真正的空间轨迹；不能因英文共用 move 或 motion 就在中文里统一写成运动或轨迹"
            ),
            "源文若是缺少标点的连续名词，要先恢复并列关系；replacement 必须逐句默读，不能产生多层名词硬拼或动作‘变成某个高度/距离’的语义错位",
        ],
        "output": "返回 checked_count 和 changes；checked_count 等于输入数量。changes 每项含 id、replacement、reason，reason 最多20字。",
    }
    started_at = time.perf_counter()
    raw = llm_runtime.complete_json(
        system_prompt=(
            "你是中文科技视频终稿质检编辑。通读完整原文和中文稿，只返回确有必要的稀疏修改，"
            "不要统一抹平人物口吻，不要逐句解释，只返回约定 JSON。"
        ),
        user_payload=payload,
        profile_id=profile_id,
        temperature=0.05,
        max_tokens=10_000,
        timeout=300,
    )
    raw_changes = raw.get("changes") if isinstance(raw, dict) else None
    required_change_ids = {item["id"] for item in payload_items if item["must_repair_numbers"]}
    if (
        not isinstance(raw, dict)
        or raw.get("checked_count") != len(bundles)
        or not isinstance(raw_changes, list)
        or any(not isinstance(item, dict) for item in raw_changes)
    ):
        raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "本土化终审没有返回完整检查结果。")
    by_id = {item["id"]: item for item in bundles}
    changes_by_id = {str(item.get("id")): item for item in raw_changes}
    if (
        len(changes_by_id) != len(raw_changes)
        or not set(changes_by_id) <= set(by_id)
        or not required_change_ids <= set(changes_by_id)
    ):
        raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "本土化终审返回了无效的修改范围。")
    unresolved_focus = []
    for item_id, decision in changes_by_id.items():
        replacement = _text(decision.get("replacement"), 8000)
        remaining_focus = _localized_bundle_review_focus(by_id[item_id]["source"], replacement)
        if replacement and remaining_focus:
            unresolved_focus.append(
                {
                    "id": item_id,
                    "source": by_id[item_id]["source"],
                    "current": replacement,
                    "review_focus": remaining_focus,
                }
            )
    repair_request_count = 0
    if unresolved_focus:
        repair_request_count = 1
        repaired_raw = llm_runtime.complete_json(
            system_prompt=(
                "你是中文科技视频终稿修复编辑。上一轮改写仍有明确的中文搭配或语义关系错误。"
                "只修复点名问题，保持事实、数字、人物口吻和信息覆盖，只返回约定 JSON。"
            ),
            user_payload={
                "task": f"{LOCALIZATION_PROMPT_VERSION}:quality-review-repair",
                "items": unresolved_focus,
                "rules": [
                    "先按 source 判断人物动作、物体运动、车辆行驶、运镜、构图和空间位置之间的真实关系",
                    "源 ASR 缺少标点时恢复并列关系，不要把并列项硬拼成多层定语",
                    "逐句默读 replacement；不能保留 review_focus 点名的问题，也不能让动作本身变成高度、距离或场景",
                    "不得增加原文没有的事实、术语、解释或网络化表达",
                ],
                "output": "返回 items 数组；每项仅含与输入一致的 id、replacement、reason，数量、顺序和 id 必须完全一致。",
            },
            profile_id=profile_id,
            temperature=0.02,
            max_tokens=min(2_000, max(600, len(unresolved_focus) * 180)),
            timeout=180,
        )
        repaired_rows = repaired_raw.get("items") if isinstance(repaired_raw, dict) else None
        expected_repair_ids = [item["id"] for item in unresolved_focus]
        returned_repair_ids = [
            str(item.get("id")) for item in repaired_rows or [] if isinstance(item, dict)
        ]
        if not isinstance(repaired_rows, list) or returned_repair_ids != expected_repair_ids:
            raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "本土化终审修复没有返回完整结果。")
        for repaired in repaired_rows:
            item_id = str(repaired["id"])
            replacement = _text(repaired.get("replacement"), 8000)
            if not replacement or _localized_bundle_review_focus(by_id[item_id]["source"], replacement):
                raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "本土化终审修复后仍有明确表达问题。")
            changes_by_id[item_id] = {
                **changes_by_id[item_id],
                "replacement": replacement,
                "reason": _text(repaired.get("reason"), 120) or changes_by_id[item_id].get("reason"),
            }
    reviewed = []
    changes = []
    for source in bundles:
        decision = changes_by_id.get(source["id"])
        replacement = _text(decision.get("replacement"), 8000) if decision else ""
        text = replacement or source["text"]
        if not _numbers_preserved(source["source"], text):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_NUMBER_CHANGED",
                "本土化终审后仍有数字与原文不一致，任务已停止写入。",
                {
                    "bundle_id": source["id"],
                    "source_numbers": list(_normalized_numbers(source["source"]).elements()),
                    "target_numbers": list(_normalized_numbers(text).elements()),
                },
            )
        if replacement and replacement != source["text"]:
            changes.append(
                {
                    "id": source["id"],
                    "before": source["text"],
                    "after": replacement,
                    "reason": _text(decision.get("reason"), 120) or "修正了中文表达",
                }
            )
        reviewed.append({**source, "text": text})
    return reviewed, changes, {
        "planned_batch_count": 1,
        "request_count": 1 + repair_request_count,
        "split_count": 0,
        "duration_ms": _elapsed_ms(started_at),
    }


def _localized_bundle_review_focus(source: str, text: str) -> list[str]:
    focus = []
    if "干净" in text:
        focus.append("检查‘干净’是否在机械翻译 clean；按对象改成清晰、自然、瑕疵少、完成度高或能直接用")
    if "体现不了" in text or "体现不出" in text:
        focus.append("检查‘体现不了/体现不出’是否笼统生硬；优先说观众具体看不出的画质、细节、规模或效果")
    if re.search(r"(?:榨干|榨取).{0,8}(?:AI|效果|价值|潜力)", text, flags=re.IGNORECASE):
        focus.append("避免把 squeeze the most out of 直译成榨干或榨取，改成把能力发挥到极致或做出最佳效果")
    if "这就是如何" in text:
        focus.append("‘这就是如何’多半保留了英文句法，改成直接自然的中文陈述")
    if "我将" in text:
        focus.append("教程口播中的‘我将’通常过于书面，按语气改成接下来我来、我会或直接说动作")
    if any(marker in text for marker in ("进行创建", "从而实现", "获得效果", "向你展示怎样")):
        focus.append("存在名词化或英文语序，改成简短主动的中文口语")
    focus.extend(_motion_localization_review_focus(source, text))
    return focus


def _motion_localization_review_focus(source: str, chinese: str) -> list[str]:
    source_lower = source.lower()
    focus: list[str] = []
    if re.search(r"\b(?:my|his|her|their)\s+(?:walk|walking|gait)\b", source_lower) and re.search(
        r"(?:我的|他的|她的|他们的)(?:走路|行走)(?!动作|姿态)", chinese
    ):
        focus.append("人物的 walk/gait 指走路时的动作、步态或姿态，不能按英语所有格直译成‘我的走路’一类生硬名词短语")
    if re.search(r"\b(?:camera|handheld)\s+(?:move|movement|motion)\b", source_lower) and any(
        marker in chinese for marker in ("摄像机运动", "摄影机运动", "相机运动", "手持运动", "摄像机动", "摄影机动")
    ):
        focus.append("camera/handheld move 在影视语境通常是运镜、镜头移动或手持运镜；结合句意选择，不能机械写成‘摄像机运动’或‘手持运动’")
    if re.search(r"\bmoving\s+camera\b", source_lower) and any(
        marker in chinese for marker in ("移动相机", "移动摄影机", "移动摄像机")
    ):
        focus.append("moving camera 描述镜头处于运动状态，通常写移动镜头、运动镜头或运镜，不能写成像在搬设备的‘移动相机’")
    if re.search(r"\bcamera\s+movement\b", source_lower) and "镜头" in chinese and not any(
        marker in chinese for marker in ("运镜", "镜头移动", "移动镜头", "运动镜头", "镜头在动", "镜头动")
    ):
        focus.append("camera movement 指运镜或镜头移动；不能只写成某个‘镜头’而丢掉运动方式")
    if re.search(r"\b(?:driv\w*\s+)?motion\b", source_lower) and re.search(r"(?:开车|行车).{0,4}运动", chinese):
        focus.append("driving motion 要结合上下文说明行车动作、连续运动或构图关系，不能笼统写成‘开车的运动’，也不要无依据改成轨迹")
    if re.search(r"\b(?:rig|framing)\b", source_lower) and any(
        marker in chinese for marker in ("架设角度", "摄影机架设", "摄像机架设")
    ):
        focus.append("rig/framing 要按画面语境判断机位、构图、取景或拍摄装置，不能默认拼成生硬的‘摄影机架设角度’")
    if (
        re.search(r"\brig\b.*\bframing\b.*\b(?:driv\w*\s+)?motion\b", source_lower)
        and re.search(
            r"(?:装置|机位|构图|取景)(?:的|对).{0,12}(?:行车|开车).{0,8}(?:动作|运动|取景|构图)",
            chinese,
        )
    ):
        focus.append("源 ASR 可能漏掉并列标点；分别判断拍摄装置、机位/构图和行车动作是否为并列保护项，不能硬拼成多层名词定语")
    if re.search(r"\b(?:camera|handheld)\s+(?:move|movement|motion)\b", source_lower) and re.search(
        r"(?:运镜|镜头移动).{0,8}变成.{0,12}(?:高|远|近)", chinese
    ):
        focus.append("运镜本身不会变成某个高度或距离；按原文关系写成原先拍下的运镜被搬到、出现在或呈现在新的空间")
    return focus


def _review_timed_localization(
    timed: list[dict],
    *,
    profile_id: str,
    is_cancelled: CancelCallback | None,
) -> tuple[list[dict], list[dict], dict]:
    if not timed:
        return [], [], {"planned_batch_count": 0, "request_count": 0, "duration_ms": 0}
    started_at = time.perf_counter()
    long_timeline = len(timed) > TIMED_REVIEW_BATCH_MAX_ITEMS
    batches = [_timed_review_risk_window(timed)] if long_timeline else _timed_review_batches(timed)
    number_groups = _timed_number_group_map(timed)

    request_count = 0
    fallback_count = 0

    def review_batch(batch_index: int, first_index: int, items: list[dict]) -> tuple[list[dict], list[dict]]:
        nonlocal fallback_count, request_count
        _ensure_active(is_cancelled)
        last_index = first_index + len(items)
        context_start = max(0, first_index - 4)
        context_end = min(len(timed), last_index + 4)

        def compact(item: dict) -> dict:
            number_group = number_groups.get(item["id"])
            return {
                "id": item["id"],
                "t": [item["start_ms"], item["end_ms"]],
                "source": item["source_text"],
                "zh": item["tts_text"],
                "required_numbers": (
                    [] if number_group else list(_normalized_numbers(item["source_text"]).elements())
                ),
                "number_mapping_group": number_group,
                "cps": item.get("cps"),
                "flags": item.get("quality_flags") or [],
            }

        payload = {
            "task": f"{LOCALIZATION_PROMPT_VERSION}:timed-review-detect",
            "batch": batch_index + 1,
            "coverage": {
                "mode": "risk_window" if long_timeline else "full_timeline",
                "window_start": first_index,
                "window_end": last_index,
                "total_item_count": len(timed),
            },
            "items": [compact(item) for item in items],
            "neighbor_context": [
                {**compact(item), "read_only": True}
                for item in timed[context_start:first_index] + timed[last_index:context_end]
            ],
            "rules": [
                "先通读本批和只读相邻上下文，再逐条核对同一时间段的源语与中文语义是否对应",
                "只修改事实遗漏或增加、相邻字幕语义错位、数字归属错误、切断紧密结构，或 flags 明确标出的阅读速度问题",
                "number_mapping_group 非空时，组内 member_ids 的中文合计必须把 required_numbers 各保留一次；版本号被源语切成 2. 和 0 时按连续的 2.0 理解",
                "number_mapping_group 为空时，每条修改后的中文必须保留该条 required_numbers，不能交换不同字幕的数字",
                "需要把中文意义在相邻条目间重新分配时，issue_ids 必须包含每个受影响 id",
                "不得修改时间、顺序、id 或来源；时间码只能帮助理解顺序和时长，不能用来臆测真实开口",
                "这一层不再做文风润色或翻译腔普查；没有明确映射或硬约束问题的条目不要放入 changes",
                f"issue_ids 最多 {TIMED_REVIEW_MAX_CHANGES_PER_BATCH} 项；优先报告语义和事实错误，不得为填满数量而上报",
            ],
            "output": (
                "只返回 issue_ids 和 has_more_critical_issues，不得返回字幕正文、修改文本、解释、逐条检查结果或其他字段。"
                "issue_ids 仅列确有关键问题的 id；没有问题时返回空数组。达到上限后仍有明确关键问题时，"
                "has_more_critical_issues 必须为 true。"
            ),
        }
        request_count += 1
        try:
            raw = llm_runtime.complete_json(
                system_prompt=(
                    "你是双语影视字幕时间线质检员。先通读输入的完整连续范围，只定位源语与中文时间段的明确语义映射错误、"
                    "事实错误和硬性阅读速度问题。不要重写字幕，不要解释，只返回少量问题 id 的约定 JSON。"
                ),
                user_payload=payload,
                profile_id=profile_id,
                temperature=0.05,
                max_tokens=512,
                timeout=300,
            )
        except llm_runtime.LlmRuntimeError as exc:
            if exc.code != "llm_output_truncated":
                raise
            fallback_count += 1
            return items, []

        # Keep accepting the former one-pass response shape for compatibility
        # with queued work and tests, while production prompts use the bounded
        # detect-then-repair protocol below.
        legacy_changes = raw.get("changes") if isinstance(raw, dict) else None
        if isinstance(legacy_changes, list):
            raw_changes = legacy_changes
            issue_ids = [str(item.get("id")) for item in raw_changes if isinstance(item, dict)]
        else:
            raw_issue_ids = raw.get("issue_ids") if isinstance(raw, dict) else None
            if not isinstance(raw_issue_ids, list) or any(not isinstance(item, str) for item in raw_issue_ids):
                raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "时间线终审没有返回有效的问题范围。")
            issue_ids = [str(item) for item in raw_issue_ids]
            raw_changes = []

        expected_ids = {item["id"] for item in items}
        if (
            not isinstance(raw, dict)
            or any(not isinstance(item, dict) for item in raw_changes)
            or len(issue_ids) != len(set(issue_ids))
            or not set(issue_ids) <= expected_ids
        ):
            raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "时间线终审返回了无效的问题范围。")
        if (
            len(issue_ids) > TIMED_REVIEW_MAX_CHANGES_PER_BATCH
            or raw.get("has_more_critical_issues") is True
            or (
                len(issue_ids) == TIMED_REVIEW_MAX_CHANGES_PER_BATCH
                and raw.get("has_more_critical_issues") is not False
            )
        ):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_TIMED_REVIEW_OVERFLOW",
                "时间线终审发现的问题超过本次安全修改上限，未覆盖当前本土化字幕轨。",
                {"batch": batch_index + 1, "returned_changes": len(issue_ids)},
            )

        if legacy_changes is None and issue_ids:
            issue_indexes = {index for index, item in enumerate(items) if item["id"] in set(issue_ids)}
            context_indexes = {
                nearby
                for index in issue_indexes
                for nearby in range(max(0, index - 3), min(len(items), index + 4))
            }
            repair_payload = {
                "task": f"{LOCALIZATION_PROMPT_VERSION}:timed-review-repair",
                "issue_ids": issue_ids,
                "editable_items": [compact(items[index]) for index in sorted(issue_indexes)],
                "ordered_context": [
                    {**compact(items[index]), "editable": index in issue_indexes}
                    for index in sorted(context_indexes)
                ],
                "rules": [
                    "只修复 issue_ids 中已经确认的关键问题，不得修改其他 id",
                    "需要重新分配相邻字幕语义时，返回每个受影响 id 的完整 text",
                    "保留事实、数字、品牌、否定、因果和人物意图，不得修改时间、顺序、id 或来源",
                    "中文必须适合当前时长；没有把握的项目可以不改，不能为了有结果而扩写",
                ],
                "output": (
                    "只返回 changes 数组。每项仅含 id、完整替换 text、最多24字的 reason；"
                    "只允许 issue_ids 中的 id，没有必要修改时返回空数组。"
                ),
            }
            request_count += 1
            repaired = llm_runtime.complete_json(
                system_prompt=(
                    "你是双语影视字幕时间线修订编辑。问题范围已经由全文检查确定，现在只对指定 id 做最小修复，"
                    "保留原作者口吻和事实，只返回约定 JSON。"
                ),
                user_payload=repair_payload,
                profile_id=profile_id,
                temperature=0.05,
                max_tokens=4_000,
                timeout=300,
            )
            raw_changes = repaired.get("changes") if isinstance(repaired, dict) else None
            if (
                not isinstance(raw_changes, list)
                or any(not isinstance(item, dict) for item in raw_changes)
            ):
                raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "时间线终审没有返回有效的修复结果。")

        change_ids = [str(item.get("id")) for item in raw_changes]
        if (
            len(change_ids) != len(set(change_ids))
            or not set(change_ids) <= set(issue_ids)
        ):
            raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "时间线终审返回了无效的字幕修改范围。")
        replacements = {str(item["id"]): item for item in raw_changes}
        reviewed = []
        changes = []
        for source in items:
            decision = replacements.get(source["id"])
            spoken_text = _text(decision.get("text"), 1000) if decision else ""
            if not spoken_text or spoken_text == source["tts_text"]:
                reviewed.append(source)
                continue
            replacement = {
                **source,
                "tts_text": spoken_text,
                "display_text": _normalize_display_text(spoken_text),
                "quality_flags": [],
            }
            if _candidate_exceeds_budget(replacement):
                reviewed.append(source)
                continue
            reviewed.append(replacement)
            changes.append(
                {
                    "id": source["id"],
                    "before": source["display_text"],
                    "after": _normalize_display_text(spoken_text),
                    "reason": _text(decision.get("reason"), 120) or "修正了语义映射或断句",
                }
            )
        return reviewed, changes

    results: list[tuple[list[dict], list[dict]] | None] = [None] * len(batches)
    if len(batches) > 1 and TIMED_REVIEW_MAX_PARALLEL_BATCHES > 1:
        with ThreadPoolExecutor(max_workers=min(TIMED_REVIEW_MAX_PARALLEL_BATCHES, len(batches))) as executor:
            futures = {
                executor.submit(review_batch, index, first_index, batch): index
                for index, (first_index, batch) in enumerate(batches)
            }
            for future in as_completed(futures):
                _ensure_active(is_cancelled)
                results[futures[future]] = future.result()
    else:
        for index, (first_index, batch) in enumerate(batches):
            results[index] = review_batch(index, first_index, batch)

    if long_timeline:
        reviewed = [{**item, "quality_flags": list(item.get("quality_flags") or [])} for item in timed]
        for (first_index, _batch), result in zip(batches, results):
            if result is not None:
                reviewed[first_index : first_index + len(result[0])] = result[0]
    else:
        reviewed = [item for result in results if result is not None for item in result[0]]
    changes = [item for result in results if result is not None for item in result[1]]
    if [item["id"] for item in reviewed] != [item["id"] for item in timed]:
        raise AppException(422, "VIDEO_LOCALIZATION_REVIEW_INVALID", "时间线终审结果顺序不完整。")
    restored_number_ids = _validate_timed_review_numbers(timed, reviewed)
    if restored_number_ids:
        changes = [item for item in changes if item["id"] not in restored_number_ids]
    return reviewed, changes, {
        "planned_batch_count": len(batches),
        "request_count": request_count,
        "fallback_count": fallback_count,
        "timed_review_mode": (
            "llm_risk_window"
            if long_timeline and not fallback_count
            else "deterministic_long_timeline_fallback"
            if long_timeline
            else "llm_sparse"
            if not fallback_count
            else "deterministic_fallback"
        ),
        "reviewed_start_index": batches[0][0] if long_timeline else 0,
        "reviewed_item_count": sum(len(batch) for _first_index, batch in batches),
        "total_item_count": len(timed),
        "reviewed_end_index": batches[-1][0] + len(batches[-1][1]),
        "duration_ms": _elapsed_ms(started_at),
    }


def _timed_review_risk_window(timed: list[dict]) -> tuple[int, list[dict]]:
    """Choose one bounded semantic window for an oversized timeline review."""
    window_size = min(len(timed), TIMED_REVIEW_BATCH_MAX_ITEMS)
    midpoint = len(timed) // 2

    def item_risk(index: int) -> int:
        item = timed[index]
        score = 0
        flags = item.get("quality_flags") or []
        score += min(5, len(flags)) * 20
        if _candidate_exceeds_hard_budget(item):
            score += 120
        elif _candidate_exceeds_budget(item):
            score += 70
        if not _numbers_preserved(item.get("source_text") or "", item.get("tts_text") or ""):
            score += 100
        if not _normalize_display_text(item.get("tts_text") or ""):
            score += 150
        if item.get("adaptation_note") or item.get("research_usage"):
            score += 25
        if index:
            previous = timed[index - 1]
            if (
                _normalize_display_text(previous.get("tts_text") or "")
                == _normalize_display_text(item.get("tts_text") or "")
            ):
                score += 40
            if set(previous.get("source_cue_ids") or []) & set(item.get("source_cue_ids") or []):
                score += 15
        return score

    scores = [item_risk(index) for index in range(len(timed))]

    def boundary_penalty(start: int) -> int:
        penalty = 0
        end = start + window_size
        for boundary in (start, end):
            if not 0 < boundary < len(timed):
                continue
            left = timed[boundary - 1]
            right = timed[boundary]
            if left.get("source_bundle_id") and left.get("source_bundle_id") == right.get("source_bundle_id"):
                penalty += 5
            if set(left.get("source_cue_ids") or []) & set(right.get("source_cue_ids") or []):
                penalty += 3
        return penalty

    start = max(
        range(len(timed) - window_size + 1),
        key=lambda candidate: (
            sum(scores[candidate : candidate + window_size]),
            -boundary_penalty(candidate),
            -abs(candidate + window_size // 2 - midpoint),
        ),
    )
    return start, timed[start : start + window_size]


def _timed_review_batches(timed: list[dict]) -> list[tuple[int, list[dict]]]:
    if len(timed) <= TIMED_REVIEW_BATCH_MAX_ITEMS or TIMED_REVIEW_MAX_BATCHES <= 1:
        return [(0, timed)]

    if len(timed) > TIMED_REVIEW_BATCH_MAX_ITEMS * TIMED_REVIEW_MAX_BATCHES:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_TIMED_REVIEW_TOO_LARGE",
            "本土化字幕过长，无法在本次限定复核批次内稳定处理。",
            {
                "item_count": len(timed),
                "max_items": TIMED_REVIEW_BATCH_MAX_ITEMS * TIMED_REVIEW_MAX_BATCHES,
            },
        )

    midpoint = len(timed) // 2
    radius = max(8, min(30, len(timed) // 10))
    candidates = range(max(1, midpoint - radius), min(len(timed), midpoint + radius + 1))

    def boundary_score(index: int) -> tuple[int, int]:
        left = timed[index - 1]
        right = timed[index]
        gap_ms = max(0, int(right["start_ms"]) - int(left["end_ms"]))
        sentence_bonus = 300 if str(left.get("source_text") or "").rstrip().endswith((".", "?", "!")) else 0
        return gap_ms + sentence_bonus, -abs(index - midpoint)

    split = max(candidates, key=boundary_score)
    return [(0, timed[:split]), (split, timed[split:])]


def _validate_timed_review_numbers(timed: list[dict], reviewed: list[dict]) -> set[str]:
    number_groups = _timed_number_group_map(timed)
    restored_ids: set[str] = set()
    index = 0
    while index < len(timed):
        end = index + 1
        group = number_groups.get(timed[index]["id"])
        if group:
            end = index + len(group["member_ids"])
        source_text = _join_semantic_sources([item["source_text"] for item in timed[index:end]])
        target_text = " ".join(item["tts_text"] for item in reviewed[index:end])
        if not _numbers_preserved(source_text, target_text):
            original_text = " ".join(item["tts_text"] for item in timed[index:end])
            if _numbers_preserved(source_text, original_text):
                reviewed[index:end] = timed[index:end]
                restored_ids.update(item["id"] for item in timed[index:end])
            else:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_NUMBER_CHANGED",
                    "时间线终审改变了数字或把数字移到了无关字幕，任务已停止写入。",
                    {"subtitle_ids": [item["id"] for item in timed[index:end]]},
                )
        index = end
    return restored_ids


def _timed_number_group_map(timed: list[dict]) -> dict[str, dict]:
    mismatches = {
        index
        for index, item in enumerate(timed)
        if not _numbers_preserved(item["source_text"], item["tts_text"])
    }
    result: dict[str, dict] = {}
    mismatch_ids = {timed[index]["id"] for index in mismatches}
    bundle_members: dict[str, list[dict]] = defaultdict(list)
    for item in timed:
        bundle_id = _text(item.get("source_bundle_id"), 120)
        if bundle_id:
            bundle_members[bundle_id].append(item)

    # Chinese is written for a complete semantic bundle before being split
    # back onto the timeline. A number may cross a number-free subtitle inside
    # that bundle, so validate the whole semantic unit rather than only a run
    # of adjacent mismatches.
    group_number = 0
    covered_ids: set[str] = set()
    for members in bundle_members.values():
        if not any(item["id"] in mismatch_ids for item in members):
            continue
        group_number += 1
        source_text = _join_semantic_sources([item["source_text"] for item in members])
        group = {
            "id": f"number_group_{group_number:03d}",
            "member_ids": [item["id"] for item in members],
            "required_numbers": list(_normalized_numbers(source_text).elements()),
        }
        for item in members:
            result[item["id"]] = group
            covered_ids.add(item["id"])

    index = 0
    while index < len(timed):
        if index not in mismatches or timed[index]["id"] in covered_ids:
            index += 1
            continue
        end = index + 1
        while end < len(timed) and end in mismatches and timed[end]["id"] not in covered_ids:
            end += 1
        group_number += 1
        members = timed[index:end]
        source_text = _join_semantic_sources([item["source_text"] for item in members])
        group = {
            "id": f"number_group_{group_number:03d}",
            "member_ids": [item["id"] for item in members],
            "required_numbers": list(_normalized_numbers(source_text).elements()),
        }
        for item in members:
            result[item["id"]] = group
        index = end
    return result


def _ensure_localized_timeline_constraints(timed: list[dict]) -> None:
    remaining = [
        item
        for item in timed
        if _candidate_exceeds_hard_budget(item)
        or int(item["end_ms"]) - int(item["start_ms"]) < MIN_SUBTITLE_DURATION_MS
    ]
    if not remaining:
        return
    raise AppException(
        422,
        "VIDEO_LOCALIZATION_TIMING_BUDGET_UNRESOLVED",
        "时间线终审后仍有字幕过短、过长或阅读过快，未覆盖当前本土化字幕轨。",
        {
            "count": len(remaining),
            "items": [
                {
                    "id": item["id"],
                    "source_cue_ids": item["source_cue_ids"],
                    "text": item["display_text"],
                    **_candidate_budget_report(item, max_cps=HARD_MAX_CHINESE_CPS),
                }
                for item in remaining[:20]
            ],
        },
    )


def _extend_timed_for_readability(timed: list[dict], draft: VideoLocalizationDraft) -> None:
    media_end = int(draft.source_media.duration_ms or 0) or None
    for index, item in enumerate(timed):
        duration = int(item["end_ms"]) - int(item["start_ms"])
        required_duration = max(
            MIN_SUBTITLE_DURATION_MS,
            math.ceil(_reading_units(item["display_text"]) * 1000 / MAX_CHINESE_CPS),
        )
        deficit = min(MAX_SUBTITLE_DURATION_MS - duration, required_duration - duration)
        if deficit <= 0:
            continue

        following = timed[index + 1] if index + 1 < len(timed) else None
        right_limit = int(following["start_ms"]) if following is not None else media_end
        if right_limit is not None:
            take = min(deficit, max(0, right_limit - int(item["end_ms"])))
            item["end_ms"] += take
            deficit -= take

        if deficit <= 0:
            continue
        previous = timed[index - 1] if index else None
        left_limit = int(previous["end_ms"]) if previous is not None else 0
        take = min(deficit, 120, max(0, int(item["start_ms"]) - left_limit))
        item["start_ms"] -= take


def _localized_bundles_to_candidates(bundles: list[dict], draft: VideoLocalizationDraft) -> list[dict]:
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    cue_order = {cue.cue_id: index for index, cue in enumerate(draft.cues)}
    cue_by_word: dict[str, list[str]] = defaultdict(list)
    for cue in draft.cues:
        for word_id in cue.source_word_ids:
            cue_by_word[word_id].append(cue.cue_id)

    candidates = []
    for bundle in bundles:
        target_chunks = _split_spoken_chinese(bundle["text"])
        mappings = _monotonic_length_alignment(bundle["units"], target_chunks)
        for mapping_index, (source_units, target_group) in enumerate(mappings):
            group_word_ids = [word_id for unit in source_units for word_id in unit["source_word_ids"]]
            word_partitions = _partition_ids_by_text_weight(group_word_ids, target_group)
            for target_text, source_word_ids in zip(target_group, word_partitions):
                source_cue_ids = sorted(
                    {cue_id for word_id in source_word_ids for cue_id in cue_by_word.get(word_id, [])},
                    key=lambda cue_id: cue_order.get(cue_id, len(cue_order)),
                )
                if not source_cue_ids:
                    source_cue_ids = list(dict.fromkeys(cue_id for unit in source_units for cue_id in unit["source_cue_ids"]))
                source_text = _join_source_words(
                    [word_by_id[word_id].text for word_id in source_word_ids if word_id in word_by_id]
                ) or _join_semantic_sources([unit["source"] for unit in source_units])
                candidates.append(
                    {
                        "source_bundle_id": bundle["id"],
                        "source_cue_ids": source_cue_ids,
                        "source_word_ids": source_word_ids,
                        "source_text": source_text,
                        "display_text": _normalize_display_text(target_text),
                        "tts_text": target_text,
                        "adaptation_note": bundle.get("adaptation_note") if mapping_index == 0 else "",
                        "research_usage": bundle.get("research_usage") or [],
                        "quality_flags": [],
                    }
                )
    _validate_candidate_source_coverage(candidates, draft)
    return candidates


def _validate_candidate_source_coverage(candidates: list[dict], draft: VideoLocalizationDraft) -> None:
    expected_cue_ids = {cue.cue_id for cue in draft.cues if (cue.en_subtitle_text or "").strip()}
    returned_cue_ids = {cue_id for item in candidates for cue_id in item["source_cue_ids"]}
    known_word_ids = {word.word_id for word in (draft.transcription.words if draft.transcription else [])}
    expected_word_ids = [
        word_id
        for cue in draft.cues
        if (cue.en_subtitle_text or "").strip()
        for word_id in cue.source_word_ids
        if word_id in known_word_ids
    ]
    returned_word_ids = [word_id for item in candidates for word_id in item["source_word_ids"]]
    if not expected_cue_ids <= returned_cue_ids or (expected_word_ids and returned_word_ids != expected_word_ids):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_COVERAGE_INCOMPLETE",
            "本土化字幕遗漏、重复或打乱了部分原文，任务已停止写入。",
            {
                "missing_cue_ids": sorted(expected_cue_ids - returned_cue_ids)[:20],
                "expected_word_count": len(expected_word_ids),
                "returned_word_count": len(returned_word_ids),
            },
        )


def _split_spoken_chinese(text: str) -> list[str]:
    sentences = [item.strip() for item in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", text) if item.strip()]
    chunks: list[str] = []
    for sentence in sentences or [text.strip()]:
        pieces = [item for item in re.findall(r"[^，,：:]+[，,：:]?", sentence) if item.strip()]
        current = ""
        for piece in pieces:
            if current and _reading_units(current + piece) > LOCALIZATION_TARGET_CUE_VISIBLE_CHARS:
                chunks.extend(_hard_split_spoken_piece(current))
                current = ""
            current += piece
        if current:
            chunks.extend(_hard_split_spoken_piece(current))
    return [chunk.strip() for chunk in chunks if _normalize_display_text(chunk)]


def _hard_split_spoken_piece(text: str) -> list[str]:
    if _reading_units(text) <= MAX_CHARS_PER_LINE * 2:
        return [text]
    result = []
    remaining = text
    while _reading_units(remaining) > MAX_CHARS_PER_LINE * 2:
        limit = _character_index_for_reading_units(remaining, LOCALIZATION_TARGET_CUE_VISIBLE_CHARS)
        candidates = [
            match.start()
            for match in re.finditer(r"(?:但是|不过|所以|然后|而且|如果|因为|就是|同时|只要|甚至|还是|以及|或者|但|而)", remaining)
            if 10 <= match.start() <= MAX_CHARS_PER_LINE * 2
        ]
        split = min(candidates, key=lambda value: abs(value - limit)) if candidates else limit
        while 0 < split < len(remaining) and remaining[split - 1].isascii() and remaining[split].isascii():
            split -= 1
        split = split or limit
        result.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip()
    if remaining:
        result.append(remaining)
    return result


def _character_index_for_reading_units(text: str, target_units: int) -> int:
    best = 1
    for index in range(1, len(text)):
        if _reading_units(text[:index]) > target_units:
            break
        best = index
    return min(best, len(text) - 1)


def _monotonic_length_alignment(source_units: list[dict], target_chunks: list[str]) -> list[tuple[list[dict], list[str]]]:
    source_weights = [max(1, int(unit.get("word_count") or len(unit.get("source_word_ids") or []))) for unit in source_units]
    target_weights = [max(1, _reading_units(text)) for text in target_chunks]
    ratio = sum(target_weights) / max(1, sum(source_weights))
    source_anchor_counts = _alignment_anchor_counts(" ".join(unit.get("source") or "" for unit in source_units))
    target_anchor_counts = _alignment_anchor_counts(" ".join(target_chunks))
    shared_anchors = set(source_anchor_counts) & set(target_anchor_counts)
    source_count, target_count = len(source_units), len(target_chunks)
    costs = [[math.inf] * (target_count + 1) for _ in range(source_count + 1)]
    paths: list[list[tuple[int, int] | None]] = [[None] * (target_count + 1) for _ in range(source_count + 1)]
    costs[0][0] = 0.0
    for source_index in range(source_count + 1):
        for target_index in range(target_count + 1):
            if not math.isfinite(costs[source_index][target_index]):
                continue
            for source_take in range(1, min(3, source_count - source_index) + 1):
                for target_take in range(1, min(3, target_count - target_index) + 1):
                    source_weight = sum(source_weights[source_index : source_index + source_take])
                    target_weight = sum(target_weights[target_index : target_index + target_take])
                    length_cost = abs(math.log((target_weight + 1) / (source_weight * ratio + 1)))
                    source_group_anchors = _alignment_anchor_counts(
                        " ".join(
                            unit.get("source") or ""
                            for unit in source_units[source_index : source_index + source_take]
                        )
                    )
                    target_group_anchors = _alignment_anchor_counts(
                        " ".join(target_chunks[target_index : target_index + target_take])
                    )
                    anchor_cost = 2.5 * sum(
                        abs(source_group_anchors[anchor] - target_group_anchors[anchor])
                        for anchor in shared_anchors
                    )
                    merge_cost = 0.08 * max(0, source_take + target_take - 2)
                    next_cost = costs[source_index][target_index] + length_cost + anchor_cost + merge_cost
                    next_source = source_index + source_take
                    next_target = target_index + target_take
                    if next_cost < costs[next_source][next_target]:
                        costs[next_source][next_target] = next_cost
                        paths[next_source][next_target] = (source_take, target_take)
    if not math.isfinite(costs[source_count][target_count]):
        return [(source_units, target_chunks)]
    reversed_groups = []
    source_index, target_index = source_count, target_count
    while source_index or target_index:
        step = paths[source_index][target_index]
        if step is None:
            return [(source_units, target_chunks)]
        source_take, target_take = step
        reversed_groups.append(
            (
                source_units[source_index - source_take : source_index],
                target_chunks[target_index - target_take : target_index],
            )
        )
        source_index -= source_take
        target_index -= target_take
    return list(reversed(reversed_groups))


def _alignment_anchor_counts(text: str) -> Counter:
    numbers = [f"number:{value}" for value in _normalized_numbers(text).elements()]
    latin_tokens = [
        f"term:{token.casefold()}"
        for token in re.findall(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9_.-]{1,}|\d+[A-Za-z]+)(?![\w])", text)
    ]
    return Counter([*numbers, *latin_tokens])


def _partition_ids_by_text_weight(word_ids: list[str], texts: list[str]) -> list[list[str]]:
    if len(texts) <= 1:
        return [word_ids]
    weights = [max(1, _reading_units(text)) for text in texts]
    total_weight = sum(weights)
    partitions = []
    cursor = 0
    consumed = 0
    for index, weight in enumerate(weights):
        consumed += weight
        boundary = len(word_ids) if index == len(weights) - 1 else round(len(word_ids) * consumed / total_weight)
        minimum_boundary = min(len(word_ids), cursor + 1)
        remaining_targets = len(weights) - index - 1
        maximum_boundary = max(minimum_boundary, len(word_ids) - remaining_targets)
        boundary = max(minimum_boundary, min(boundary, maximum_boundary))
        partitions.append(word_ids[cursor:boundary])
        cursor = boundary
    return partitions


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
    if len(batches) >= 2 and len(batches[-1][1]) < len(batches[-2][1]):
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
    _allow_parallel: bool = True,
    _context_cues: list[VideoLocalizationCue] | None = None,
    _context_start: int = 0,
) -> list[dict]:
    cues = [cue for cue in draft.cues if (cue.en_subtitle_text or "").strip()]
    context_cues = _context_cues or cues
    cue_by_id = {cue.cue_id: cue for cue in cues}
    word_by_id = {word.word_id: word for word in (draft.transcription.words if draft.transcription else [])}
    allowed_research_ids = {
        str(item.get("question_id")) for item in research.get("questions") or [] if item.get("question_id")
    }
    batches = _localization_batches(cues)
    if _allow_parallel and len(batches) > 1 and LOCALIZATION_MAX_PARALLEL_BATCHES > 1:
        batch_results: list[list[dict] | None] = [None] * len(batches)

        def run_planned_batch(batch_index: int, batch_start: int, batch: list[VideoLocalizationCue]) -> list[dict]:
            sub_draft = draft.model_copy(update={"cues": batch})
            return _localize_cues(
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
                on_repair=(
                    (lambda _batch_number, _total: on_repair(batch_index + 1, len(batches)))
                    if on_repair
                    else None
                ),
                on_split=(
                    (lambda _batch_number, _total: on_split(batch_index + 1, len(batches))) if on_split else None
                ),
                _allow_parallel=False,
                _context_cues=context_cues,
                _context_start=_context_start + batch_start,
            )

        with ThreadPoolExecutor(max_workers=min(LOCALIZATION_MAX_PARALLEL_BATCHES, len(batches))) as executor:
            futures = {
                executor.submit(run_planned_batch, batch_index, batch_start, batch): batch_index
                for batch_index, (batch_start, batch) in enumerate(batches)
            }
            completed = 0
            for future in as_completed(futures):
                _ensure_active(is_cancelled)
                batch_results[futures[future]] = future.result()
                completed += 1
                on_batch(completed, len(batches))
                if on_batch_preview:
                    on_batch_preview(
                        [item for result in batch_results if result is not None for item in result]
                    )
        return [item for result in batch_results if result is not None for item in result]

    candidates: list[dict] = []
    for batch_index, (batch_start, batch) in enumerate(batches):
        _ensure_active(is_cancelled)
        context_batch_start = _context_start + batch_start
        allowed_ids = {cue.cue_id for cue in batch}
        allowed_word_ids = {word_id for cue in batch for word_id in cue.source_word_ids if word_id in word_by_id}
        compact_contract = _compact_localization_contract(batch, word_by_id)
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
                    for cue in context_cues[max(0, context_batch_start - 2) : context_batch_start]
                ],
                "next": [
                    {"cue_id": cue.cue_id, "text": cue.en_subtitle_text}
                    for cue in context_cues[
                        context_batch_start + len(batch) : context_batch_start + len(batch) + 2
                    ]
                ],
            },
            "source_cues": compact_contract["source_cues"],
            "pause_boundaries": compact_contract["pause_boundaries"],
            "rules": {
                "immutable": ["事实", "数字", "专名", "否定", "因果", "比较", "人物意图", "情绪强度"],
                "text": "只返回一份可直接配音的自然中文；上屏文本由代码移除普通标点生成，不要重复返回两份字幕",
                "segmentation": (
                    "按完整语义和源音频停顿重新分段，不必与源 cue 一一对应，可合并或拆分。"
                    f"当一段预计超过 {PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS} 个可见中文字，且输入给出至少 "
                    f"{PREFERRED_PAUSE_SPLIT_MIN_GAP_MS} 毫秒的停顿边界时，只要边界两侧都能成为自然中文语义单位，优先拆成两段。"
                    "不要在专名、固定搭配、动宾结构、数量词、否定结构或因果连接中间硬拆。"
                    "第一次生成就必须满足上屏限制；只在输入 cue 边界处合并或拆分，不要把同一个 cue 重复分给多段"
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
                    "calculation": "片段时长取 cue_range 首条的 start_ms 到末条的 end_ms",
                },
                "note": "仅在确有文化转述、术语消歧或重要表达调整时填写，最多18个汉字；通常省略",
                "research": "只有查证资料实际改变当前表达时才填写；没有直接影响就省略",
            },
            "output": (
                "返回 segments 数组。每项只需 cue_range 和 text；cue_range 是输入编号的闭区间 [起点,终点]。"
                "text 保留配音需要的标点和语气，上屏文本由代码生成。可选 note；仅实际采用查证时返回 research=[{id,effect}]。"
                "所有 cue_range 必须按顺序连续覆盖本批全部 cue，不能遗漏、重复或打乱；同一个 cue 只能属于一个 segment。"
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
            for sub_offset, sub_batch in ((0, batch[:midpoint]), (midpoint, batch[midpoint:])):
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
                    _allow_parallel=False,
                    _context_cues=context_cues,
                    _context_start=context_batch_start + sub_offset,
                )
                split_items.extend(sub_items)
            return split_items

        try:
            raw = llm_runtime.complete_json(
                system_prompt=(
                    "你是中文影视字幕本土化编辑，不是逐句翻译器。先通读上下文，保持说话行为、事实和人物口吻，"
                    "再改写成中国大陆创作者面对镜头时自然会说的口语。中文必须能直接说出口，不能保留英语语序、书面腔或生硬直译。"
                    "不要逐字硬译，不要擅自植入中国历史人物、地名、典故或网络热梗。只写一份自然口语，代码会据此生成无普通标点的上屏字幕。"
                    "源字幕行只是识别容器，不是目标语言分段边界。只返回约定 JSON。"
                ),
                user_payload=payload,
                profile_id=profile_id,
                temperature=0.18,
                max_tokens=20000,
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
            raw_segments = _expand_compact_localized_segments(raw_segments, compact_contract)
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
        base_duration = int(item["end_ms"]) - int(item["start_ms"])
        if base_duration <= MAX_SUBTITLE_DURATION_MS + END_HOLD_MS:
            desired_end = min(desired_end, int(item["start_ms"]) + MAX_SUBTITLE_DURATION_MS)
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
    request_state = {"started_at": time.perf_counter(), "requests": 0, "lock": Lock()}
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

        def refine_batch(indexes: list[int]) -> tuple[dict[int, list[dict]], int]:
            _ensure_active(is_cancelled)
            batch_started_at = time.perf_counter()
            entries = [(index, fitted[index], timed_by_index[index]) for index in indexes]
            if all(_candidate_only_needs_compression(timed) for _index, _item, timed in entries):
                try:
                    result = _compress_reading_speed_batch(
                        entries,
                        profile_id=profile_id,
                        is_cancelled=is_cancelled,
                        request_state=request_state,
                    )
                except (AppException, llm_runtime.LlmRuntimeError):
                    result = _refine_candidate_batch(
                        entries,
                        cue_by_id=cue_by_id,
                        word_by_id=word_by_id,
                        context=context,
                        profile_id=profile_id,
                        source_language=source_language,
                        target_language=target_language,
                        is_cancelled=is_cancelled,
                        request_state=request_state,
                    )
            else:
                result = _refine_candidate_batch(
                    entries,
                    cue_by_id=cue_by_id,
                    word_by_id=word_by_id,
                    context=context,
                    profile_id=profile_id,
                    source_language=source_language,
                    target_language=target_language,
                    is_cancelled=is_cancelled,
                    request_state=request_state,
                )
            return result, max(1, round(time.perf_counter() - batch_started_at))

        if len(batches) > 1 and LOCALIZATION_FIT_MAX_PARALLEL_BATCHES > 1:
            if on_progress:
                on_progress(
                    0,
                    len(batches),
                    round_number,
                    f"并行处理 {len(batches)} 组共 {len(problem_indexes)} 段",
                )
            with ThreadPoolExecutor(
                max_workers=min(LOCALIZATION_FIT_MAX_PARALLEL_BATCHES, len(batches))
            ) as executor:
                futures = {executor.submit(refine_batch, indexes): indexes for indexes in batches}
                completed = 0
                for future in as_completed(futures):
                    _ensure_active(is_cancelled)
                    batch_replacements, elapsed_seconds = future.result()
                    replacements.update(batch_replacements)
                    completed += 1
                    if on_progress:
                        on_progress(completed, len(batches), round_number, f"本组耗时 {elapsed_seconds} 秒")
                    if on_preview:
                        on_preview(_apply_fit_replacements(fitted, replacements))
        else:
            for batch_index, indexes in enumerate(batches, start=1):
                if on_progress:
                    on_progress(batch_index - 1, len(batches), round_number, f"正在处理 {len(indexes)} 段")
                batch_replacements, elapsed_seconds = refine_batch(indexes)
                replacements.update(batch_replacements)
                if on_progress:
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


def _candidate_exceeds_hard_budget(item: dict) -> bool:
    return bool(_candidate_budget_report(item, max_cps=HARD_MAX_CHINESE_CPS)["violations"])


def _candidate_has_preferred_pause_split(item: dict, word_by_id: dict) -> bool:
    if _reading_units(item.get("display_text") or "") < PREFERRED_PAUSE_SPLIT_MIN_VISIBLE_CHARS:
        return False
    ordered = [word_by_id[word_id] for word_id in item.get("source_word_ids") or [] if word_id in word_by_id]
    if len(ordered) < 4:
        return False
    return any(
        int(right.start_ms) - int(left.end_ms) >= PREFERRED_PAUSE_SPLIT_MIN_GAP_MS
        for left, right in zip(ordered[1:-2], ordered[2:-1])
    )


def _candidate_budget_report(item: dict, *, max_cps: float = MAX_CHINESE_CPS) -> dict:
    duration = max(1, int(item["end_ms"]) - int(item["start_ms"]))
    reading_units = _reading_units(item["display_text"])
    cps = reading_units * 1000 / duration
    violations = []
    if duration > MAX_SUBTITLE_DURATION_MS:
        violations.append("时长超过8秒，需要按完整语义拆分")
    if reading_units > MAX_CHARS_PER_LINE * 2:
        violations.append("上屏文字超过32个字，需要拆分或精简")
    if cps > max_cps:
        violations.append(f"阅读速度超过每秒{max_cps:g}字，需要在不丢信息的前提下精简表达")
    return {
        "duration_ms": duration,
        "visible_chars": reading_units,
        "reading_units": reading_units,
        "cps": round(cps, 2),
        "max_chars_for_duration": max(
            1,
            min(MAX_CHARS_PER_LINE * 2, math.floor(duration * max_cps / 1000)),
        ),
        "suggested_min_segments": max(
            1,
            math.ceil(duration / MAX_SUBTITLE_DURATION_MS),
            math.ceil(reading_units / (MAX_CHARS_PER_LINE * 2)),
        ),
        "violations": violations,
    }


def _candidate_only_needs_compression(item: dict) -> bool:
    report = _candidate_budget_report(item)
    return (
        report["suggested_min_segments"] == 1
        and len(report["violations"]) == 1
        and report["violations"][0].startswith("阅读速度")
    )


def _compress_candidate_locally(source: dict, timed: dict) -> dict | None:
    max_chars = _candidate_budget_report(timed)["max_chars_for_duration"]
    text = str(source.get("tts_text") or source.get("display_text") or "").strip()
    replacements = (
        ("我做这个视频的原因是", "原因是"),
        ("我做这期视频的原因是", "原因是"),
        ("看起来像电影里出来的", "就像电影里的"),
        ("并在过程中", "同时"),
        ("向你的镜头添加东西", "给镜头加东西"),
        ("好了 这就是", "这就是"),
        ("它里面", "里面"),
        ("每一个", "每个"),
        ("任何一个", "任何"),
        ("接下来", "下面"),
        ("比如说", "比如"),
        ("看一下", "看看"),
        ("来看看", "看看"),
        ("自己的", "自己"),
        ("能够", "能"),
        ("可以", "能"),
        ("已经", "已"),
        ("仍然", "还"),
        ("依然", "还"),
        ("更加", "更"),
        ("最为", "最"),
        ("整个", "全"),
        ("全部", "全"),
        ("然后", "再"),
        ("而且", "还"),
        ("但是", "但"),
        ("就是", "就"),
        ("这个", "这"),
        ("那个", "那"),
        ("当中", "里"),
        ("之中", "里"),
        ("马上就", "马上"),
        ("直接就", "直接"),
        ("根本就", "根本"),
    )
    for before, after in replacements:
        if _reading_units(_normalize_display_text(text)) <= max_chars:
            break
        text = text.replace(before, after, 1)
    replacement = {
        **source,
        "tts_text": text,
        "display_text": _normalize_display_text(text),
        "quality_flags": [],
    }
    if (
        not text
        or replacement["tts_text"] == source.get("tts_text")
        or not _numbers_preserved(source["source_text"], text)
        or _candidate_exceeds_budget({**timed, **replacement})
    ):
        return None
    return replacement


def _compress_reading_speed_batch(
    entries: list[tuple[int, dict, dict]],
    *,
    profile_id: str,
    is_cancelled: CancelCallback | None,
    request_state: dict,
) -> dict[int, list[dict]]:
    _ensure_active(is_cancelled)
    payload_items = [
        {
            "id": f"candidate_{index:04d}",
            "source": item["source_text"],
            "chinese": item["tts_text"] or item["display_text"],
            "current_visible_chars": _reading_units(item["display_text"]),
            "max_visible_chars": _candidate_budget_report(timed)["max_chars_for_duration"],
        }
        for index, item, timed in entries
    ]
    timeout = _claim_fit_request(request_state)
    raw = llm_runtime.complete_json(
        system_prompt=(
            "你是中文影视字幕精简编辑。每条中文已经完成全文本土化，现在只把略微超出阅读速度的句子压缩到指定字数。"
            "保持原意、数字、专名、否定、因果和口语语气，不拆分，不解释，不逐字翻译，只返回约定 JSON。"
        ),
        user_payload={
            "task": f"{LOCALIZATION_PROMPT_VERSION}:compress-reading-speed",
            "items": payload_items,
            "rules": [
                "每项必须返回一条完整、自然、可直接配音的中文 text",
                "text 去除普通标点后的可见字数不得超过 max_visible_chars",
                "不得省略 source 与 chinese 中的事实、数字、品牌、否定、因果或人物意图",
                "不要返回逐词时间、来源 ID、分析或备选句",
            ],
            "output": "只返回 items 数组，每项仅含与输入一致的 id 和 text；数量、顺序、id 必须完全一致。",
        },
        profile_id=profile_id,
        temperature=0.05,
        max_tokens=min(1_600, max(600, len(entries) * 120)),
        timeout=timeout,
    )
    rows = raw.get("items") if isinstance(raw, dict) else None
    expected_ids = [item["id"] for item in payload_items]
    returned_ids = [str(item.get("id")) for item in rows or [] if isinstance(item, dict)]
    if not isinstance(rows, list) or returned_ids != expected_ids:
        raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "字幕精简没有返回完整结果。")

    replacements: dict[int, list[dict]] = {}
    for (index, source, timed), row in zip(entries, rows):
        text = _text(row.get("text"), 1000)
        replacement = {
            **source,
            "tts_text": text,
            "display_text": _normalize_display_text(text),
            "quality_flags": [],
        }
        if (
            not text
            or not _numbers_preserved(source["source_text"], text)
            or _candidate_exceeds_budget({**timed, **replacement})
        ):
            raise AppException(422, "VIDEO_LOCALIZATION_FIT_INVALID", "字幕精简结果仍未满足字数或事实限制。")
        replacements[index] = [replacement]
    return replacements


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
                    "text": item["tts_text"] or item["display_text"],
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
                "每段上屏字幕不超过32个可见字符，源语时间范围不超过8秒，阅读速度不超过9.5字/秒。"
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
                    "每个 segment 只需包含一份可直接配音的 text；上屏文本由代码移除普通标点生成。"
                    "仅在确有重要表达调整时可附带最多18个汉字的 note。"
                    "不要回传冗长的来源 ID 数组；每个 parent 必须连续覆盖到最后一个词或 cue。"
                ),
            },
            profile_id=profile_id,
            temperature=0.08,
            max_tokens=8000,
            timeout=timeout,
            allow_array=True,
        )
    except llm_runtime.LlmRuntimeError as exc:
        recoverable_output_codes = {
            "llm_json_invalid",
            "llm_json_not_object",
            "llm_output_truncated",
            "llm_response_invalid",
        }
        if exc.code not in recoverable_output_codes:
            raise
        if len(entries) <= 1:
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
    lock = request_state.get("lock")
    with lock if lock is not None else nullcontext():
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
        raw = _expand_compact_text_fields(raw)
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
    split_count = 0
    batches = _quality_review_batches(timed)
    # Each planned batch may need one binary recovery pass: the original
    # request plus two smaller requests. Keep a little extra room for a nested
    # split while the wall-clock limit remains the final safety boundary.
    request_state = {
        "started_at": started_at,
        "requests": 0,
        "max_requests": max(QUALITY_REVIEW_MAX_REQUESTS, len(batches) * 4),
    }
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
                                "text": item["tts_text"] or item["display_text"],
                                "duration_ms": item["end_ms"] - item["start_ms"],
                                "review_focus": _localization_review_focus(item, context),
                            }
                            for item in items
                        ],
                        "output": (
                            "返回 checked_count 和 changes。checked_count 必须等于输入项目数；"
                            "review_focus 非空的项目必须逐条完成所列检查；确认有问题时必须放入 changes，确认无问题才可不修改。"
                            "changes 只包含确实需要修改的项目，每项包含 id、可直接配音的 text、简短 reason。"
                            "上屏文本由代码移除普通标点生成；reason 最多18个汉字。"
                            "没有修改时 changes 返回空数组。"
                        ),
                    },
                    profile_id=profile_id,
                    temperature=0.05,
                    max_tokens=12000,
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
                else expected_ids
                if isinstance(raw, dict)
                and isinstance(raw.get("checked_count"), int)
                and not isinstance(raw.get("checked_count"), bool)
                and raw.get("checked_count") == len(expected_ids)
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
                spoken_text = _text(decision.get("text"), 1000)
                if spoken_text:
                    display_text = _normalize_display_text(spoken_text) or source["display_text"]
                    tts_text = spoken_text
                else:
                    display_text = (
                        _normalize_display_text(_text(decision.get("display_text"), 800))
                        or source["display_text"]
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
                            "reason": _text(decision.get("reason"), 120) or "终审修正了语义或口语表达",
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
                "request_limit": int(request_state["max_requests"]),
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
    focus.extend(_motion_localization_review_focus(source, display))
    return focus


def _claim_quality_review_request(request_state: dict) -> float:
    elapsed = time.perf_counter() - float(request_state["started_at"])
    requests = int(request_state["requests"])
    max_requests = int(request_state.get("max_requests", QUALITY_REVIEW_MAX_REQUESTS))
    if requests >= max_requests or elapsed >= QUALITY_REVIEW_MAX_SECONDS:
        raise AppException(
            504,
            "VIDEO_LOCALIZATION_REVIEW_LIMIT_REACHED",
            "字幕语义复核已达到本次任务的处理上限，未覆盖当前本土化字幕轨。请稍后重试。",
            {"request_count": requests, "request_limit": max_requests, "elapsed_ms": round(elapsed * 1000)},
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
        flags.difference_update(
            {
                "localized_duration_short",
                "localized_duration_long",
                "localized_reading_speed_high",
                "localized_text_too_long",
            }
        )
        duration = max(1, item["end_ms"] - item["start_ms"])
        reading_units = _reading_units(item["display_text"])
        cps = reading_units * 1000 / duration
        if duration < MIN_SUBTITLE_DURATION_MS:
            flags.add("localized_duration_short")
        if duration > MAX_SUBTITLE_DURATION_MS:
            flags.add("localized_duration_long")
        if cps > MAX_CHINESE_CPS:
            flags.add("localized_reading_speed_high")
        if reading_units > MAX_CHARS_PER_LINE * 2:
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
    local_adjustment_count = int(diagnostics.get("local_adjustment_count") or 0)
    if request_count:
        summary = f"把初稿中的超长或阅读过快片段调整为 {len(after)} 段可上屏字幕。"
    elif local_adjustment_count:
        summary = f"利用时间余量与保守中文缩写，本地精简了 {local_adjustment_count} 段阅读过快字幕。"
    else:
        summary = "初次中文生成已满足字幕长度与阅读速度要求，无需额外调用模型返修。"
    return _result(
        "warning" if diagnostics.get("unresolved_count") else "success",
        summary,
        [
            ("调整前片段", len(before)),
            ("调整后片段", len(after)),
            ("本地精简", local_adjustment_count),
            ("模型请求", request_count),
            ("调整轮数", int(diagnostics.get("round_count") or 0)),
        ],
        [("逐轮处理记录", rounds)],
    )


def _bundle_mapping_step_result(bundles: list[dict], candidates: list[dict], diagnostics: dict) -> dict:
    return _result(
        "success",
        "中文口播完成后，已用单调对齐把语义段映射回源音频时间；时间戳没有发送给语言模型。",
        [
            ("全文语义段", len(bundles)),
            ("生成字幕片段", len(candidates)),
            ("本土化请求", int(diagnostics.get("request_count") or 0)),
            ("请求体", f"{round(int(diagnostics.get('payload_bytes') or 0) / 1024, 1)} KB"),
        ],
        notes=["逐词时间只在本地用于排序、停顿和初步字幕时间，不参与中文改写。"],
    )


def _post_review_constraint_step_result(before: list[dict], after: list[dict], diagnostics: dict) -> dict:
    before_violations = sum(_candidate_exceeds_budget(item) for item in before)
    after_target_warnings = sum(_candidate_exceeds_budget(item) for item in after)
    after_hard_violations = sum(_candidate_exceeds_hard_budget(item) for item in after)
    request_count = int(diagnostics.get("request_count") or 0)
    return _result(
        "warning" if after_target_warnings else "success",
        (
            f"终审后重新检查 {len(before)} 段字幕，精简 {before_violations - after_target_warnings} 段；"
            f"{after_target_warnings} 段略高于目标但未超过导出硬上限，建议试听确认。"
            if before_violations
            else f"终审后重新检查 {len(before)} 段字幕，没有产生新的时长、字数或阅读速度问题。"
        ),
        [
            ("重新检查", len(before)),
            ("二次返修", before_violations),
            ("返修后片段", len(after)),
            ("模型请求", request_count),
            ("建议试听", after_target_warnings),
            ("剩余硬性超限", after_hard_violations),
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
    timed_review_mode = diagnostics.get("timed_review_mode")
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
    used_local_timeline_audit = timed_review_mode in {
        "deterministic_long_timeline",
        "deterministic_fallback",
    }
    summary = (
        f"全文中文已完成语义终审；时间线阶段用本地规则检查了 {len(items)} 段字幕，"
        f"修正 {len(changes)} 段表达，{len(risky)} 段建议人工试听确认。"
        if used_local_timeline_audit
        else f"终审检查了 {len(items)} 段字幕，修正 {len(changes)} 段表达；{len(risky)} 段建议人工试听确认。"
    )
    notes = []
    if timed_review_mode == "deterministic_long_timeline":
        notes.append("长时间线不重复发送给模型；本阶段只检查来源覆盖、数字、顺序、时长、字数和阅读速度。")
    elif timed_review_mode == "deterministic_fallback":
        notes.append("模型未按稀疏协议返回，时间线阶段已改用本地硬校验；全文中文终审结果不受影响。")
    return _result(
        "warning" if risky or timed_review_mode == "deterministic_fallback" else "success",
        summary,
        [
            ("复核字幕", len(items)),
            ("表达修正", len(changes)),
            ("建议试听", len(risky)),
            ("计划批次", diagnostics.get("planned_batch_count", 0)),
            ("模型请求", diagnostics.get("request_count", 0)),
            ("失败拆分", diagnostics.get("split_count", 0)),
            ("模型降级", diagnostics.get("timed_review_fallback_count", 0)),
        ],
        [("终审修正", samples), ("需要重点看的字幕", warnings)],
        notes=notes,
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


def _compact_localization_contract(cues: list[VideoLocalizationCue], word_by_id: dict) -> dict:
    cue_ids = [cue.cue_id for cue in cues]
    cue_word_ids = [
        [word_id for word_id in cue.source_word_ids if word_id in word_by_id]
        for cue in cues
    ]
    source_cues = []
    for cue_index, cue in enumerate(cues, start=1):
        duration_ms = max(1, int(cue.end_ms or 0) - int(cue.start_ms or 0))
        source_cues.append(
            {
                "i": cue_index,
                "speaker": cue.speaker_id,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "max_chars": max(
                    1,
                    min(MAX_CHARS_PER_LINE * 2, math.floor(duration_ms * MAX_CHINESE_CPS / 1000)),
                ),
                "text": cue.en_subtitle_text,
            }
        )

    pause_boundaries = [
        {
            "after_cue": index,
            "gap_ms": max(0, int(right.start_ms or 0) - int(left.end_ms or 0)),
        }
        for index, (left, right) in enumerate(zip(cues, cues[1:]), start=1)
        if int(right.start_ms or 0) - int(left.end_ms or 0) >= 120
    ]
    return {
        "cue_ids": cue_ids,
        "cue_word_ids": cue_word_ids,
        "source_cues": source_cues,
        "pause_boundaries": pause_boundaries,
    }


def _expand_compact_localized_segments(raw_segments, contract: dict) -> list:
    if not isinstance(raw_segments, list):
        return raw_segments
    cue_ids = contract.get("cue_ids") or []
    cue_word_ids = contract.get("cue_word_ids") or []
    expanded = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            expanded.append(raw)
            continue
        item = _expand_compact_text_fields(raw)
        if not item.get("source_cue_ids"):
            cue_range = _compact_index_range(item.get("cue_range"), len(cue_ids))
            item["source_cue_ids"] = cue_ids[cue_range[0] - 1 : cue_range[1]] if cue_range else []
        if not item.get("source_word_ids") and item.get("source_cue_ids"):
            selected_cues = set(item["source_cue_ids"])
            item["source_word_ids"] = [
                word_id
                for cue_id, word_ids in zip(cue_ids, cue_word_ids)
                if cue_id in selected_cues
                for word_id in word_ids
            ]

        expanded.append(item)
    return expanded


def _expand_compact_text_fields(raw: dict) -> dict:
    item = dict(raw)
    spoken_text = _text(item.get("text"), 1000)
    if spoken_text:
        item["tts_text"] = spoken_text
        item["display_text"] = _normalize_display_text(spoken_text)
    if not item.get("adaptation_note"):
        item["adaptation_note"] = _text(item.get("note"), 120)
    if not item.get("research_usage"):
        item["research_usage"] = _compact_research_usage(item.get("research"))
    return item


def _compact_index_range(value, size: int) -> tuple[int, int] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    start, end = value
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 1
        or end < start
        or end > size
    ):
        return None
    return start, end


def _compact_research_usage(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for raw in value[:8]:
        if isinstance(raw, dict):
            question_id = _text(raw.get("id") or raw.get("question_id"), 80)
            effect = _text(raw.get("effect"), 300)
        elif isinstance(raw, list) and len(raw) == 2:
            question_id = _text(raw[0], 80)
            effect = _text(raw[1], 300)
        else:
            continue
        if question_id and effect:
            result.append({"question_id": question_id, "effect": effect})
    return result


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


def _reading_units(value: str) -> int:
    """Count Han characters individually and contiguous Latin/number terms as words."""
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:[._+%/_-][A-Za-z0-9]+)*", value))


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
