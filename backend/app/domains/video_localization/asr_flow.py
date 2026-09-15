"""Whole-document ASR review flow: understand, focus, then recheck."""

from __future__ import annotations

import copy
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Protocol

from app.domains.video_localization import (
    entity_variant_safety,
    llm_observability,
    research_limits,
    review_decisions as review_decisions_domain,
    review_warning_policy,
    visual_evidence as visual_evidence_domain,
    web_research,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationGlossaryEntry,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.services import llm_runtime


PROMPT_VERSION = "asr-flow-v8"
MAX_REVIEW_ROUNDS = 1
REVIEW_OVERLAP_SEGMENTS = 6
LARGE_WINDOW_MAX_CHARS = 24_000
LENGTH_ERROR_CODES = frozenset({"llm_context_too_long", "llm_output_truncated"})
RETRYABLE_OUTPUT_CODES = frozenset({"llm_json_invalid", "llm_json_not_object", "llm_output_truncated"})
ASR_STEP_PLAN = (
    ("asr", "生成原始听写", 10),
    ("understand_document", "理解全文并规划复查", 30),
    ("visual_evidence", "画面取证", 35),
    ("research", "核对名称与背景", 40),
    ("normalize_entities", "统一名称与术语", 45),
    ("section_review_r1", "定位听写疑点", 50),
    ("review_decisions_r1", "重听并应用明确修改", 60),
    ("whole_recheck_r1", "本地收尾检查", 70),
    ("transcript_quality_gate", "进入校时前检查", 290),
    ("alignment", "对齐逐词时间", 300),
    ("audio_boundaries", "分析声音停顿", 310),
    ("boundary_review", "本地确定字幕断句", 320),
    ("subtitle_track", "生成并检查字幕轨", 330),
)
ASR_STEP_ORDER = {step_id: order for step_id, _label, order in ASR_STEP_PLAN}
ASR_PLANNED_STEP_IDS = frozenset(
    step_id for step_id, _label, _order in ASR_STEP_PLAN
)
_REVIEW_ROUND_LABELS = {
    1: (
        "定位听写疑点",
        "重听并应用明确修改",
        "本地收尾检查",
    ),
}

ProgressCallback = Callable[[float, str], None]
ReportCallback = Callable[[str, dict], None]
CancelCallback = Callable[[], bool]
DocumentUnderstandingRunner = Callable[..., dict]
VisualEvidenceRunner = Callable[..., object]
ResearchRunner = Callable[..., VideoLocalizationResearchState]
EntityNormalizationRunner = Callable[..., object]
SectionReviewRunner = Callable[..., object]
ReviewDecisionsRunner = Callable[..., object]
WholeRecheckRunner = Callable[..., object]
SegmentsChangedCallback = Callable[
    [str, list[VideoLocalizationTranscriptSegment]],
    None,
]


def apply_explicit_glossary(
    segments: list[VideoLocalizationTranscriptSegment],
    glossary: list[VideoLocalizationGlossaryEntry],
) -> tuple[list[VideoLocalizationTranscriptSegment], list[dict]]:
    """Apply project-owned terminology through the shared safe implementation."""

    return _apply_explicit_glossary(segments, glossary)


@dataclass(frozen=True)
class AsrReviewRun:
    segments: list[VideoLocalizationTranscriptSegment]
    research: VideoLocalizationResearchState
    profile_id: str | None
    model_id: str | None
    report: dict
    stage_timings: dict[str, dict]
    review_meta: dict


@dataclass(frozen=True)
class AsrDocumentUnderstandingRun:
    brief: dict
    profile_id: str
    model_id: str
    raw_responses: list[dict]
    duration_ms: int
    execution_strategy: str
    llm_call_count: int
    retry_count: int
    llm_calls: list[llm_observability.AsrLlmCallRecord] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class AsrDocumentUnderstandingCompletionRequest:
    """One explicit model attempt within document understanding."""

    contract_version: str
    behavior_version: str
    call_id: str
    attempt: int
    system_prompt: str
    user_payload: dict
    profile_id: str
    max_tokens: int
    timeout: float
    disable_reasoning: bool
    trace_sink: llm_runtime.TraceSink | None = None


class AsrDocumentUnderstandingCompletionGateway(Protocol):
    """Replaceable boundary used by both direct and managed execution."""

    def __call__(
        self,
        request: AsrDocumentUnderstandingCompletionRequest,
    ) -> dict: ...



@dataclass
class _Recorder:
    on_progress: ProgressCallback | None
    on_report: ReportCallback | None
    results: dict[str, dict] = field(default_factory=dict)
    definitions: list[dict] = field(default_factory=list)
    next_order: int = 30
    research: VideoLocalizationResearchState | None = None

    def publish(
        self,
        step_id: str,
        label: str,
        progress: float,
        *,
        status: str,
        summary: str,
        purpose: str,
        metrics: list[dict] | None = None,
        items: list[dict] | None = None,
    ) -> None:
        existing = self.results.get(step_id)
        order = ASR_STEP_ORDER.get(step_id) or (
            int(existing.get("order")) if isinstance(existing, dict) and existing.get("order") else self.next_order
        )
        if not existing:
            self.next_order = max(self.next_order + 10, order + 10)
            self.definitions.append({"id": step_id, "label": label, "order": order})
        result = {
            "label": label,
            "order": order,
            "status": status,
            "purpose": purpose,
            "summary": summary,
            "metrics": list(metrics or []),
            "sections": [{"title": "检查结果", "items": list(items)}] if items else [],
            "notes": [],
        }
        self.results[step_id] = result
        if self.on_progress:
            self.on_progress(progress, f"flow:{step_id}|{label}")
        if self.on_report:
            self.on_report(step_id, result)


def review_transcript(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    profile_id: str | None,
    glossary: list[VideoLocalizationGlossaryEntry] | None = None,
    scene_context: str = "",
    research_cache_dir: str | Path | None = None,
    is_cancelled: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_report: ReportCallback | None = None,
    document_understanding_runner: DocumentUnderstandingRunner,
    visual_evidence_runner: VisualEvidenceRunner,
    research_runner: ResearchRunner,
    entity_normalization_runner: EntityNormalizationRunner,
    section_review_runner: SectionReviewRunner,
    review_decisions_runner: ReviewDecisionsRunner,
    whole_recheck_runner: WholeRecheckRunner,
    on_segments_changed: SegmentsChangedCallback | None = None,
) -> AsrReviewRun:
    started_at = time.perf_counter()
    source_segments = copy.deepcopy(segments)
    recorder = _Recorder(on_progress=on_progress, on_report=on_report)
    stage_timings: dict[str, dict] = {}
    try:
        return _review_transcript(
            source_segments,
            language=language,
            profile_id=profile_id,
            glossary=glossary,
            scene_context=scene_context,
            research_cache_dir=research_cache_dir,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_report=on_report,
            started_at=started_at,
            initial_changes=[],
            recorder=recorder,
            stage_timings=stage_timings,
            document_understanding_runner=document_understanding_runner,
            visual_evidence_runner=visual_evidence_runner,
            research_runner=research_runner,
            entity_normalization_runner=entity_normalization_runner,
            section_review_runner=section_review_runner,
            review_decisions_runner=review_decisions_runner,
            whole_recheck_runner=whole_recheck_runner,
            on_segments_changed=on_segments_changed,
        )
    except llm_runtime.LlmRuntimeError as exc:
        prepared, glossary_changes = _apply_explicit_glossary(
            source_segments,
            glossary or [],
        )
        _publish_segments_changed(
            on_segments_changed,
            "glossary",
            prepared,
            changes=glossary_changes,
        )
        return _degraded_run(
            prepared,
            started_at=started_at,
            error=exc,
            changes=glossary_changes,
            on_progress=on_progress,
            on_report=on_report,
            recorder=recorder,
            stage_timings=stage_timings,
        )


def _review_transcript(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    profile_id: str | None,
    glossary: list[VideoLocalizationGlossaryEntry] | None = None,
    scene_context: str = "",
    research_cache_dir: str | Path | None = None,
    is_cancelled: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_report: ReportCallback | None = None,
    started_at: float | None = None,
    initial_changes: list[dict] | None = None,
    recorder: _Recorder,
    stage_timings: dict[str, dict],
    document_understanding_runner: DocumentUnderstandingRunner,
    visual_evidence_runner: VisualEvidenceRunner,
    research_runner: ResearchRunner,
    entity_normalization_runner: EntityNormalizationRunner,
    section_review_runner: SectionReviewRunner,
    review_decisions_runner: ReviewDecisionsRunner,
    whole_recheck_runner: WholeRecheckRunner,
    on_segments_changed: SegmentsChangedCallback | None = None,
) -> AsrReviewRun:
    started_at = started_at or time.perf_counter()
    current = copy.deepcopy(segments)
    profile = _resolve_profile(profile_id)
    if not current:
        return _empty_run(current, recorder, started_at)
    if profile is None:
        current, glossary_changes = _apply_explicit_glossary(
            current,
            glossary or [],
        )
        _publish_segments_changed(
            on_segments_changed,
            "glossary",
            current,
            changes=glossary_changes,
        )
        return _unconfigured_run(
            current,
            recorder,
            started_at,
            changes=[*(initial_changes or []), *glossary_changes],
        )

    _ensure_active(is_cancelled)
    recorder.publish(
        "understand_document",
        "理解全文并规划复查",
        0.30,
        status="running",
        purpose="先通读整份转写，弄清视频在讲什么，再安排后面的检查范围。",
        summary="正在通读整份原始转写。",
    )
    stage_started = time.perf_counter()
    brief = document_understanding_runner(
        current,
        language=language,
        scene_context=scene_context,
        profile_id=profile.profile_id,
        is_cancelled=is_cancelled,
    )
    stage_timings["understand_document"] = {"duration_ms": _elapsed_ms(stage_started)}
    recorder.publish(
        "understand_document",
        "理解全文并规划复查",
        0.36,
        status="success",
        purpose="先通读整份转写，弄清视频在讲什么，再安排后面的检查范围。",
        summary=(
            f"已通读全文，分成 {len(brief['sections'])} 个检查区块，"
            f"其中 {len(brief['search_queries'])} 个问题需要查资料。"
        ),
        metrics=[
            {"label": "建议分段", "value": str(len(brief["sections"]))},
            {"label": "建议查证", "value": str(len(brief["search_queries"]))},
        ],
        items=[_brief_item(brief), *[_section_item(section) for section in brief["sections"]]],
    )

    current, glossary_changes = _apply_explicit_glossary(
        current,
        glossary or [],
    )
    _publish_segments_changed(
        on_segments_changed,
        "glossary",
        current,
        changes=glossary_changes,
    )
    initial_changes = [*(initial_changes or []), *glossary_changes]
    planned_queries = [
        web_research.PlannedQuery.model_validate(item)
        for item in brief["search_queries"]
    ]
    brief["search_queries"] = planned_queries

    visual_result = None
    _ensure_active(is_cancelled)
    recorder.publish(
            "visual_evidence",
            "画面取证",
            0.37,
            status="running",
            purpose="只查看确实需要的画面，读取字幕条、图表等直接可见信息。",
            summary="正在按全文理解提出的问题提取少量截图。",
    )
    stage_started = time.perf_counter()
    visual_result = visual_evidence_runner(
            current,
            language=language,
            scene_context=scene_context,
            profile_id=profile.profile_id,
            brief=brief,
            is_cancelled=is_cancelled,
    )
    visual_status = str(
        getattr(visual_result, "status", "not_needed")
    )
    visual_frames = list(
        getattr(visual_result, "frames", []) or []
    )
    visual_observations = list(
        getattr(visual_result, "observations", []) or []
    )
    stage_timings["visual_evidence"] = {
        "duration_ms": _elapsed_ms(stage_started),
        "frame_count": len(visual_frames),
        "observation_count": len(visual_observations),
    }
    recorder.publish(
            "visual_evidence",
            "画面取证",
            0.38,
            status=(
                "success"
                if visual_status in {"completed", "not_needed"}
                else "warning"
            ),
            purpose="只查看确实需要的画面，读取字幕条、图表等直接可见信息。",
            summary=(
                f"已提取 {len(visual_frames)} 张截图，"
                f"得到 {len(visual_observations)} 项画面观察。"
                if visual_frames
                else "本次没有需要查看的画面，或视觉能力不可用，已安全跳过。"
            ),
            metrics=[
                {"label": "截图", "value": str(len(visual_frames))},
                {
                    "label": "画面观察",
                    "value": str(len(visual_observations)),
                },
            ],
            items=(
                [
                    dict(item)
                    for item in visual_evidence_domain.reader_observation_items(
                        visual_result
                    )
                ]
                if isinstance(
                    visual_result,
                    visual_evidence_domain.AsrVisualEvidenceResult,
                )
                else []
            ),
    )

    _ensure_active(is_cancelled)
    recorder.publish(
        "research",
        "核对名称与背景",
        0.39,
        status="running",
        purpose="核对可能听错的名称和必要背景；普通句子不联网搜索。",
        summary="正在查找名称和背景疑点的可靠资料。",
    )
    stage_started = time.perf_counter()
    research_kwargs = {
        "language": language,
        "scene_context": scene_context,
        "profile_id": profile.profile_id,
        "cache_dir": research_cache_dir,
        "is_cancelled": is_cancelled,
        "planned_queries": brief["search_queries"],
    }
    research_kwargs["visual_evidence_result"] = visual_result
    research = research_runner(
        current,
        **research_kwargs,
    )
    recorder.research = research.model_copy(deep=True)
    stage_timings["research"] = {
        "duration_ms": _elapsed_ms(stage_started),
        "query_count": len(research.queries),
        "source_count": len(research.sources),
    }
    evidence = web_research.evidence_payload(research)
    recorder.publish(
        "research",
        "核对名称与背景",
        0.41,
        status=(
            "warning"
            if research.status in {"failed", "partial"}
            else "success"
        ),
        purpose="核对可能听错的名称和必要背景；普通句子不联网搜索。",
        summary=(
            f"查了 {len(research.queries)} 个问题，找到 {len(evidence)} 条可用资料。"
            if research.queries
            else "没有发现必须联网核对的名称或背景问题。"
        ),
        metrics=[
            {"label": "搜索问题", "value": str(len(research.queries))},
            {"label": "可用资料", "value": str(len(evidence))},
            {
                "label": "过滤资料",
                "value": str(max(0, len(research.sources) - len(evidence))),
            },
        ],
        items=[
            *[
                _research_query_item(query)
                for query in brief["search_queries"]
            ],
            *[_research_evidence_item(item) for item in evidence[:12]],
        ],
    )
    recorder.publish(
        "normalize_entities",
        "统一名称与术语",
        0.42,
        status="running",
        purpose="根据项目术语和已找到的证据统一名称，只改有明确依据的地方。",
        summary="正在判断规范名称并检查全文中的对应写法。",
    )
    normalization_started = time.perf_counter()
    normalized = entity_normalization_runner(
        current,
        brief=brief,
        evidence=evidence,
        research=research,
        profile_id=profile.profile_id,
        locked_changes=initial_changes,
    )
    current = copy.deepcopy(normalized.updated_segments)
    brief["resolved_entities"] = [
        item.model_dump(mode="json") for item in normalized.resolutions
    ]
    resolved_name_changes = [
        item.model_dump(mode="json") for item in normalized.changes
    ]
    resolved_name_warnings = [
        item.model_dump(mode="json") for item in normalized.warnings
    ]
    _publish_segments_changed(
        on_segments_changed,
        "normalize_entities",
        current,
        changes=resolved_name_changes,
    )
    stage_timings["normalize_entities"] = {
        "duration_ms": _elapsed_ms(normalization_started),
        "resolution_count": len(brief["resolved_entities"]),
        "change_count": len(resolved_name_changes),
        "warning_count": len(resolved_name_warnings),
    }
    recorder.publish(
        "normalize_entities",
        "统一名称与术语",
        0.43,
        status="warning" if resolved_name_warnings else "success",
        purpose="根据项目术语和已找到的证据统一名称，只改有明确依据的地方。",
        summary=(
            f"确认 {len(brief['resolved_entities'])} 个规范名称，"
            f"改正 {len(resolved_name_changes)} 处文字。"
        ),
        metrics=[
            {
                "label": "规范名称",
                "value": str(len(brief["resolved_entities"])),
            },
            {"label": "名称统一", "value": str(len(resolved_name_changes))},
            {"label": "建议复核", "value": str(len(resolved_name_warnings))},
        ],
        items=[
            *[_resolved_entity_item(item) for item in brief["resolved_entities"]],
            *[_change_item(item) for item in resolved_name_changes],
            *[_warning_item(item) for item in resolved_name_warnings[:8]],
        ],
    )

    rounds: list[dict] = []
    changes: list[dict] = [
        *[{**item, "round": 0} for item in (initial_changes or [])],
        *[{**item, "round": 0} for item in resolved_name_changes],
    ]
    warnings: list[dict] = list(resolved_name_warnings)
    final_recheck_warnings: list[str] = []
    final_recheck_passed = False
    final_recheck_summary = ""
    sections = brief["sections"]
    for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
        _ensure_active(is_cancelled)
        review_id = f"section_review_r{round_number}"
        decision_id = f"review_decisions_r{round_number}"
        recheck_id = f"whole_recheck_r{round_number}"
        review_label, decision_label, recheck_label = (
            _REVIEW_ROUND_LABELS[round_number]
        )
        review_purpose = (
            "分段查看原文、全文概要、前后文和查证资料，找出可能的"
            "听写错误；这一步只提问题，不改字幕。"
        )
        base_progress = 0.44 + (round_number - 1) * 0.12

        recorder.publish(
            review_id,
            review_label,
            base_progress,
            status="running",
            purpose=review_purpose,
            summary=f"正在检查 {len(sections)} 个区块。",
        )
        stage_started = time.perf_counter()
        atomic_review = section_review_runner(
            current,
            round_index=round_number,
            sections=sections,
            brief=brief,
            evidence=evidence,
            glossary=glossary or [],
            locked_changes=changes,
            profile_id=profile.profile_id,
        )
        if str(getattr(atomic_review, "status", "")) == "failed":
            raise llm_runtime.LlmRuntimeError(
                "所有分段复查区块都没有完成",
                code="asr_section_review_failed",
                status_code=502,
            )
        issues = [
            {
                "segment_id": item.segment_id,
                "current_excerpt": item.current_excerpt,
                "replacement": item.proposed_replacement,
                "reason": item.reason,
                "confidence": item.confidence,
                "needs_confirmation": item.needs_confirmation,
                "evidence_source_ids": list(item.evidence_source_ids),
                "section_id": item.section_id,
                "scope": getattr(item, "scope", "single_segment"),
                "target_segment_ids": list(
                    getattr(
                        item,
                        "target_segment_ids",
                        [item.segment_id],
                    )
                ),
                "origin": getattr(
                    item,
                    "origin",
                    "llm_section_review",
                ),
                "patches": [
                    patch.model_dump(mode="json")
                    for patch in getattr(item, "patches", [])
                ],
            }
            for item in atomic_review.issues
        ]
        review_warnings = [
            {
                "code": item.code,
                "segment_id": "",
                "excerpt": "",
                "message": item.message,
                "section_id": item.section_id,
            }
            for item in atomic_review.warnings
        ]
        reader_review_warnings = [
            item
            for item in review_warnings
            if review_warning_policy.warning_needs_reader_review(
                code=str(item.get("code") or ""),
                message=str(item.get("message") or ""),
            )
        ]
        warnings.extend(review_warnings)
        stage_timings[review_id] = {
            "duration_ms": _elapsed_ms(stage_started),
            "section_count": len(sections),
            "issue_count": len(issues),
        }
        recorder.publish(
            review_id,
            review_label,
            base_progress + 0.04,
            status="warning" if reader_review_warnings else "success",
            purpose=review_purpose,
            summary=f"已检查 {len(sections)} 个区块，找出 {len(issues)} 处可能的听写问题。",
            metrics=[
                {"label": "复查区块", "value": str(len(sections))},
                {"label": "可能问题", "value": str(len(issues))},
                {
                    "label": "已忽略无效建议",
                    "value": str(
                        len(review_warnings)
                        - len(reader_review_warnings)
                    ),
                },
            ],
            items=[
                *[_issue_item(issue) for issue in issues[:16]],
                *[
                    _warning_item(item)
                    for item in reader_review_warnings[:4]
                ],
            ],
        )

        applied: list[dict] = []
        unresolved: list[dict] = []
        recorder.publish(
            decision_id,
            decision_label,
            base_progress + 0.05,
            status="running",
            purpose="把本轮问题放回全文判断；能确认的直接改，拿不准的保留原文并说明要复核什么。",
            summary=f"正在判断 {len(issues)} 条修改建议是否可靠。",
        )
        stage_started = time.perf_counter()
        atomic_decisions = review_decisions_runner(
            current,
            round_index=round_number,
            issues=issues,
            brief=brief,
            evidence=evidence,
            locked_changes=changes,
            profile_id=profile.profile_id,
        )
        if str(getattr(atomic_decisions, "status", "")) == "failed":
            raise llm_runtime.LlmRuntimeError(
                "本轮疑点都没有完成判断",
                code="asr_review_decisions_failed",
                status_code=502,
            )
        current = [
            item.model_copy(deep=True)
            for item in atomic_decisions.updated_segments
        ]
        applied = [
            {
                **item.model_dump(mode="json"),
                "replacement": "",
            }
            for item in atomic_decisions.changes
        ]
        unresolved = [
            {
                "code": item.code,
                "segment_id": item.segment_id,
                "excerpt": item.excerpt,
                "message": item.message,
                "issue_id": item.issue_id,
            }
            for item in atomic_decisions.warnings
            if item.code
            in {
                "missing_decision",
                "invalid_decision",
                "needs_confirmation",
                "unsafe_change",
                "protected_change",
            }
        ]
        stage_timings[decision_id] = {
            "duration_ms": _elapsed_ms(stage_started),
            "issue_count": len(issues),
            "change_count": len(applied),
        }
        changes.extend(
            {**change, "round": round_number}
            for change in applied
        )
        warnings.extend(unresolved)
        recorder.publish(
            decision_id,
            decision_label,
            base_progress + 0.07,
            status="warning" if unresolved else "success",
            purpose="把本轮问题放回全文判断；能确认的直接改，拿不准的保留原文并说明要复核什么。",
            summary=f"已改正 {len(applied)} 处；另有 {len(unresolved)} 处没有把握，字幕保持原样。",
            metrics=[
                {"label": "实际修改", "value": str(len(applied))},
                {"label": "需人工复核", "value": str(len(unresolved))},
            ],
            items=[
                *[_change_item(item) for item in applied[:8]],
                *[
                    _warning_item(item)
                    for item in unresolved[
                        : max(0, 12 - len(applied[:8]))
                    ]
                ],
            ],
        )

        recorder.publish(
            recheck_id,
            recheck_label,
            base_progress + 0.08,
            status="running",
            purpose=(
                "用本地规则确认疑点决定、片段引用和相邻结构是否完整；"
                "不再调用模型，也不再开启第二轮全文审核。"
            ),
            summary="正在本地检查本轮修改结果。",
        )
        stage_started = time.perf_counter()
        try:
            atomic_recheck = whole_recheck_runner(
                current,
                round_index=round_number,
                brief=brief,
                evidence=evidence,
                previous_issues=issues,
                locked_changes=changes,
                profile_id=profile.profile_id,
            )
            recheck = {
                "passed": bool(atomic_recheck.passed),
                "continue_review": (
                    atomic_recheck.next_action == "review_next_round"
                ),
                "summary": atomic_recheck.summary,
                "warnings": [
                    *atomic_recheck.warnings,
                    *[
                        item.reason
                        for item in atomic_recheck.unresolved_items
                        if item.recommended_action == "manual_review"
                    ],
                ][:6],
                "sections": [
                    {
                        "id": item.section_id,
                        "start_segment": item.start_ordinal,
                        "end_segment": item.end_ordinal,
                        "role": item.role,
                        "focus": list(item.focus),
                    }
                    for item in atomic_recheck.next_sections
                ],
            }
        except llm_runtime.LlmRuntimeError as exc:
            if exc.code == "llm_cancelled":
                raise
            recheck = {
                "passed": False,
                "continue_review": False,
                "failed": True,
                "summary": "本轮修改已保留，但全文复核没有完成。",
                "warnings": [
                    "全文复核服务暂时不可用，请从当前已修改字幕继续重试。"
                ],
                "sections": [],
            }
        final_recheck_passed = recheck["passed"]
        final_recheck_summary = recheck["summary"]
        final_recheck_warnings = recheck["warnings"]
        stage_timings[recheck_id] = {"duration_ms": _elapsed_ms(stage_started)}
        rounds.append(
            {
                "round": round_number,
                "sections": len(sections),
                "issues": len(issues),
                "changes": len(applied),
                "warnings": len(unresolved),
                "summary": recheck["summary"],
            }
        )
        recorder.publish(
            recheck_id,
            recheck_label,
            base_progress + 0.10,
            status="success" if recheck["passed"] else "warning",
            purpose=(
                "用本地规则确认疑点决定、片段引用和相邻结构是否完整；"
                "不再调用模型，也不再开启第二轮全文审核。"
            ),
            summary=_recheck_step_summary(
                recheck,
                applied_count=len(applied),
            ),
            metrics=[
                {"label": "本轮修改", "value": str(len(applied))},
                {"label": "下一轮建议分段", "value": str(len(recheck["sections"]))},
            ],
            items=(
                [_warning_item(_uncertain_warning("", "", message)) for message in recheck["warnings"][:3]]
                if not recheck["passed"]
                else []
            ),
        )
        break

    for message in final_recheck_warnings[:3]:
        warnings.append(_uncertain_warning("", "", message))
    if not final_recheck_passed and not final_recheck_warnings:
        warnings.append(_uncertain_warning("", "", f"还有内容没有确认：{final_recheck_summary}"))
    warnings = _limit_user_warnings(_active_warnings(warnings, current))

    status = "passed_with_warnings" if warnings else "passed"
    step_result = {
        "status": "warning" if warnings else "success",
        "purpose": "检查整份转写里的听写错误和名称写法；能确认的已经改好，拿不准的保留原文并列出来。",
        "summary": (
            f"转写校对完成：共检查 {len(rounds)} 轮，改正 {len(changes)} 个片段，"
            f"另有 {len(warnings)} 处建议复听；流程已自动继续。"
        ),
        "metrics": [
            {"label": "全文复核轮次", "value": str(len(rounds))},
            {"label": "实际修改", "value": str(len(changes))},
            {"label": "建议复听", "value": str(len(warnings))},
        ],
        "sections": [
            *([{"title": "实际修改", "items": [_change_item(change) for change in changes[:16]]}] if changes else []),
            *(
                [{"title": "建议复听", "items": [_warning_item(item) for item in warnings[:16]]}]
                if warnings
                else []
            ),
        ],
        "notes": [],
        "coverage": {
            "mode": "complete",
            "shown_count": len(warnings),
            "total_count": len(warnings),
            "unit": "处建议复听",
        },
    }
    return AsrReviewRun(
        segments=current,
        research=research,
        profile_id=profile.profile_id,
        model_id=profile.model_id,
        report={
            "status": status,
            "prompt_version": PROMPT_VERSION,
            "assessment_rounds": len(rounds),
            "repair_rounds": sum(bool(item["changes"]) for item in rounds),
            "total_repairs": len(changes),
            "document_brief": _serializable_brief(brief),
            "rounds": rounds,
            "changes": changes,
            "warnings": warnings,
            "duration_ms": _elapsed_ms(started_at),
            "step_result": step_result,
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
        stage_timings=stage_timings,
        review_meta={
            "status": "completed",
            "profile_id": profile.profile_id,
            "model_id": profile.model_id,
            "error": None,
            "quality_flags": ["asr_flow_reviewed", f"asr_prompt:{PROMPT_VERSION}"],
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
    )


def understand_document(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    scene_context: str,
    profile_id: str | None,
    is_cancelled: CancelCallback | None,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None) = None,
    resolved_profile: llm_runtime.ResolvedProfile | None = None,
) -> AsrDocumentUnderstandingRun:
    """Run only whole-document understanding without research or text edits."""

    profile = resolved_profile or llm_runtime.resolve_profile(
        profile_id
    )
    if (
        profile_id
        and profile.profile_id != str(profile_id).strip()
    ):
        raise ValueError(
            "resolved LLM profile differs from requested profile"
        )
    started_at = time.perf_counter()
    raw_responses: list[dict] = []
    call_stats = {"logical": 0, "requests": 0}
    trace_collector = llm_observability.AsrLlmTraceCollector()
    brief = _understand_document(
        copy.deepcopy(segments),
        language=language,
        scene_context=scene_context,
        profile_id=profile.profile_id,
        is_cancelled=is_cancelled,
        raw_responses=raw_responses,
        call_stats=call_stats,
        trace_collector=trace_collector,
        completion_gateway=completion_gateway,
    )
    execution_strategy = (
        "windowed"
        if any(str(item.get("stage") or "").startswith("window_") for item in raw_responses)
        else "full_document"
    )
    return AsrDocumentUnderstandingRun(
        brief=brief,
        profile_id=profile.profile_id,
        model_id=profile.model_id,
        raw_responses=raw_responses,
        duration_ms=_elapsed_ms(started_at),
        execution_strategy=execution_strategy,
        llm_call_count=call_stats["requests"],
        retry_count=max(0, call_stats["requests"] - call_stats["logical"]),
        llm_calls=trace_collector.records(),
    )


def _understand_document(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    scene_context: str,
    profile_id: str,
    is_cancelled: CancelCallback | None,
    raw_responses: list[dict] | None = None,
    call_stats: dict[str, int] | None = None,
    trace_collector: llm_observability.AsrLlmTraceCollector | None = None,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None) = None,
) -> dict:
    prompt = (
        "Read the complete ASR transcript as one continuous document. Do not correct any words yet. "
        "Return a short reusable document brief: topic, speaking purpose, narrative or explanatory logic, speaker style, "
        "important entities/numbers, narrow web searches that may resolve real ASR uncertainty, visual questions that require "
        "direct evidence from nearby video frames, and continuous review sections. "
        "Search candidates may include proper names, specialized terms, cultural objects or references, idioms, slang, and colloquial expressions "
        "whose wording or meaning cannot be confirmed from the transcript alone; do not search ordinary wording. "
        "Request visual evidence only when the picture could materially clarify visible text, a chart, an object, or scene context. "
        "In particular, route a mid-sentence capitalized word that may be a misheard product or tool name to both a narrow search and a "
        "nearby visible-text question when the interface may show its spelling. Also route a one-off common word that conflicts with a "
        "repeated technical term to a nearby visible-text question when an on-screen label could disambiguate it. "
        "For lower-thirds or other labels that may appear shortly after a spoken introduction, use frame_strategy look_ahead; otherwise use nearby. "
        "Do not ask the visual step to identify a person from appearance. It may only read directly visible labels or describe visible content. "
        "For a film, drama, animation, or game, explicitly note fictional, stylized, constructed, or foreign-language dialogue that differs "
        "from the document's dominant language. When nearby frames may contain the production's own subtitles, request a visible-text check "
        "that transcribes those subtitles exactly; treat them as direct evidence of intended meaning and never ask the visual step to translate audio. "
        "Treat every inconsistent proper-name spelling as an unverified candidate, not as a fact; avoid using an unverified spelling "
        "as the name of the subject in the summary. Write brief metadata in concise Simplified Chinese while leaving transcript text unchanged. "
        "Every reader-facing metadata value except proper-name spellings and search queries must be written in concise Simplified Chinese: "
        "summary, logic, speaker style, entity roles, research reasons, section roles, and section focus. "
        "Each section must cover a continuous segment range and have its own focus based on that section's role. "
        "The rules must remain general for any subject. Return JSON only."
    )
    payload = {
        "language": language,
        "scene_context": scene_context[:4_000] or None,
        "document": _document_payload(segments),
        "limits": {
            "max_target_terms_per_query": (
                research_limits.MAX_RESEARCH_TARGET_TERMS
            ),
        },
        "output": {
            "content_kind": "film_or_drama|interview|tutorial|presentation|conversation|other|unknown",
            "summary": "简短内容概述",
            "logic": ["按顺序说明内容如何推进"],
            "speaker_style": "讲话方式概述",
            "language_notes": ["混合语言、风格化语言或画面字幕需要注意的地方"],
            "entities": [{"name": "candidate spelling", "role": "候选名称可能指什么", "needs_research": True}],
            "search_queries": [
                {
                    "query": "neutral narrow query",
                    "category": "proper_noun",
                    "reason": "为什么需要查证",
                    "target_terms": [],
                }
            ],
            "visual_questions": [
                {
                    "start_segment": 1,
                    "end_segment": 1,
                    "kind": "visible_text",
                    "reason": "为什么需要查看画面",
                    "question": "希望从画面直接确认什么",
                    "frame_strategy": "nearby",
                }
            ],
            "sections": [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": len(segments),
                    "role": "本段在全文中的作用",
                    "focus": ["本段需要重点检查什么"],
                }
            ],
        },
    }
    try:
        raw = _complete_understanding_json(
            prompt,
            payload,
            profile_id=profile_id,
            max_tokens=8_000,
            timeout=240,
            call_stats=call_stats,
            call_id="full_document",
            trace_collector=trace_collector,
            completion_gateway=completion_gateway,
        )
        if raw_responses is not None:
            raw_responses.append({"stage": "full_document", "attempt": 1, "response": raw})
    except llm_runtime.LlmRuntimeError as exc:
        if exc.code not in LENGTH_ERROR_CODES:
            raise
        if raw_responses is not None:
            raw_responses.append(
                {
                    "stage": "full_document",
                    "attempt": 1,
                    "error_code": exc.code,
                    "error": str(exc)[:500],
                }
            )
        raw = _understand_large_windows(
            segments,
            language=language,
            scene_context=scene_context,
            profile_id=profile_id,
            error_message=str(exc),
            is_cancelled=is_cancelled,
            raw_responses=raw_responses,
            call_stats=call_stats,
            trace_collector=trace_collector,
            completion_gateway=completion_gateway,
        )
    brief = _normalize_brief(raw, segment_count=len(segments))
    if not _brief_is_usable(brief):
        raw = _complete_understanding_json(
            "The previous document brief omitted required content. Read the same complete transcript again and return concise JSON only. "
            "Required: a real summary, ordered content logic, speaker style, unverified entity candidates, narrow search queries when needed, "
            "visual questions only when nearby frames can supply direct evidence, "
            "and continuous sections with distinct roles and focus. Except proper-name spellings and search query strings, every reader-facing "
            "metadata value must be in Simplified Chinese. Do not edit transcript text.",
            {**payload, "attempt": 2},
            profile_id=profile_id,
            max_tokens=8_000,
            timeout=240,
            call_stats=call_stats,
            call_id="full_document_repair",
            trace_collector=trace_collector,
            completion_gateway=completion_gateway,
        )
        if raw_responses is not None:
            raw_responses.append({"stage": "full_document", "attempt": 2, "response": raw})
        brief = _normalize_brief(raw, segment_count=len(segments))
    if not _brief_is_usable(brief):
        raise llm_runtime.LlmRuntimeError(
            "全文理解没有返回主题综述、表达逻辑和有效分段，已停止后续复查。",
            code="llm_json_invalid",
            status_code=502,
        )
    brief = _with_deterministic_name_visual_checks(
        segments,
        brief,
    )
    brief = _with_deterministic_language_visual_checks(
        segments,
        brief,
    )
    preferred_count = _initial_section_count(len(segments))
    minimum_count = min(preferred_count, 3)
    if len(brief["sections"]) >= minimum_count:
        return brief
    try:
        replanned = _complete_understanding_json(
            "Plan meaningful continuous review sections for this document. Do not edit or repeat the transcript. "
            "A long document needs multiple sections with distinct roles and section-specific focus; one whole-document section is not useful. "
            "Use the preferred count as guidance, but choose boundaries by meaning. Every role and focus value must be in concise "
            "Simplified Chinese. Return JSON only.",
            {
                "document_brief": {
                    "summary": brief["summary"],
                    "logic": brief["logic"],
                    "speaker_style": brief["speaker_style"],
                    "entities": brief["entities"],
                },
                "document": _document_payload(segments),
                "preferred_section_count": preferred_count,
                "output": {
                    "sections": [
                        {
                            "id": "S1",
                            "start_segment": 1,
                            "end_segment": len(segments),
                            "role": "本段作用",
                            "focus": ["本段专属检查重点"],
                        }
                    ]
                },
            },
            profile_id=profile_id,
            max_tokens=5_000,
            timeout=180,
            call_stats=call_stats,
            call_id="section_replan",
            trace_collector=trace_collector,
            completion_gateway=completion_gateway,
        )
        if raw_responses is not None:
            raw_responses.append({"stage": "section_replan", "attempt": 1, "response": replanned})
        sections = _normalize_sections(replanned.get("sections"), segment_count=len(segments), allow_empty=True)
    except llm_runtime.LlmRuntimeError:
        sections = []
    brief["sections"] = (
        sections if len(sections) >= minimum_count else _fallback_sections(len(segments), preferred_count)
    )
    return brief


def _with_deterministic_name_visual_checks(
    segments: list[VideoLocalizationTranscriptSegment],
    brief: dict,
) -> dict:
    """Add at most two grounded checks the document model did not plan."""

    result = copy.deepcopy(brief)
    entities = list(result.get("entities") or [])
    queries = list(result.get("search_queries") or [])
    questions = list(result.get("visual_questions") or [])
    known_text = " ".join(
        [
            *[
                str(item.get("name") or "")
                for item in entities
                if isinstance(item, dict)
            ],
            *[
                " ".join(
                    str(value)
                    for value in item.get("target_terms", [])
                )
                for item in queries
                if isinstance(item, dict)
                and isinstance(item.get("target_terms"), list)
            ],
        ]
    )
    known_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", known_text)
    }
    planned_question_text = " ".join(
        str(item.get("question") or "")
        for item in questions
        if isinstance(item, dict)
    ).casefold()
    added_tokens: set[str] = set()
    for ordinal, segment in enumerate(segments, start=1):
        text = segment.corrected_text or segment.raw_text
        for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
            token = match.group(0)
            key = token.casefold()
            prefix = text[: match.start()].rstrip()
            if (
                not prefix
                or prefix[-1:] in ".?!"
                or key in known_tokens
                or key in added_tokens
                or key in planned_question_text
                or not entity_variant_safety.looks_like_proper_name_candidate(
                    token
                )
            ):
                continue
            entities.append(
                {
                    "name": token,
                    "role": "句中大写词，可能是工具名或听写近音词",
                    "needs_research": True,
                }
            )
            queries.append(
                {
                    "query": f'"{token}" AI tool product',
                    "category": "proper_noun",
                    "reason": (
                        "该词在句中大写但未进入原名称计划，需要核对它是否是"
                        "工具名、产品名或听写近音词。"
                    ),
                    "target_terms": [token],
                }
            )
            questions.append(
                {
                    "start_segment": ordinal,
                    "end_segment": ordinal,
                    "kind": "visible_text",
                    "reason": (
                        f"转写中的“{token}”可能是工具或产品名称，"
                        "附近界面文字可以直接确认拼写。"
                    ),
                    "question": (
                        f"画面中是否直接显示了与“{token}”对应的工具或产品名称？"
                        "只抄录完整可见文字，不根据图标或上下文猜测。"
                    ),
                    "frame_strategy": "nearby",
                }
            )
            added_tokens.add(key)
            known_tokens.add(key)
            if len(added_tokens) >= 2 or len(questions) >= 12:
                break
        if len(added_tokens) >= 2 or len(questions) >= 12:
            break
    result["entities"] = entities[:40]
    result["search_queries"] = queries
    result["visual_questions"] = questions[:12]
    return result


def _with_deterministic_language_visual_checks(
    segments: list[VideoLocalizationTranscriptSegment],
    brief: dict,
) -> dict:
    """Route isolated non-Latin film dialogue to original on-screen text.

    This is an evidence request only. It never translates audio or edits the
    transcript, so a production subtitle can later be reviewed without being
    confused with an ASR guess.
    """

    result = copy.deepcopy(brief)
    questions = list(result.get("visual_questions") or [])
    if len(questions) >= 12:
        return result
    document_text = " ".join(
        segment.corrected_text or segment.raw_text
        for segment in segments
    )
    latin_count = len(re.findall(r"[A-Za-z]", document_text))
    japanese_count = len(
        re.findall(r"[぀-ヿ]", document_text)
    )
    if latin_count < 50 or japanese_count < 3 or latin_count <= japanese_count * 2:
        return result

    existing_ranges = {
        (
            int(item.get("start_segment") or 0),
            int(item.get("end_segment") or 0),
        )
        for item in questions
        if isinstance(item, dict)
        and "字幕" in str(item.get("question") or "")
    }
    for ordinal, segment in enumerate(segments, start=1):
        text = segment.corrected_text or segment.raw_text
        if len(re.findall(r"[぀-ヿ]", text)) < 3:
            continue
        if any(start <= ordinal <= end for start, end in existing_ranges):
            continue
        questions.append(
            {
                "start_segment": ordinal,
                "end_segment": ordinal,
                "kind": "visible_text",
                "reason": (
                    "这段对白的文字脚本与全片主要语言不同，"
                    "原片上屏字幕可能是创作方给出的正式语义证据。"
                ),
                "question": (
                    "画面底部或对白附近是否显示了与这段特殊语言对白"
                    "对应的原片字幕？逐字抄录完整可见字幕；"
                    "不要根据音频翻译或补写。"
                ),
                "frame_strategy": "nearby",
            }
        )
        if len(questions) >= 12:
            break
    result["visual_questions"] = questions[:12]
    return result


def _understand_large_windows(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    scene_context: str,
    profile_id: str,
    error_message: str,
    is_cancelled: CancelCallback | None,
    raw_responses: list[dict] | None = None,
    call_stats: dict[str, int] | None = None,
    trace_collector: llm_observability.AsrLlmTraceCollector | None = None,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None) = None,
) -> dict:
    partials = []
    for window_index, window in enumerate(_large_windows(segments), start=1):
        _ensure_active(is_cancelled)
        partial = _complete_understanding_json(
            "The full transcript request was too large. Understand this large continuous window and propose review ranges within the core only. "
            "Write every reader-facing metadata value in concise Simplified Chinese, including the summary, logic, speaker style, entity roles, "
            "research reasons, section roles, and section focus. Keep proper names, search query strings, target terms, and transcript quotes in "
            "their original language. Return JSON only.",
            {
                "fallback_reason": error_message[:500],
                "language": language,
                "scene_context": scene_context[:2_000] or None,
                "context_before": _document_payload(
                    window["before"],
                    start_ordinal=window["before_start_ordinal"],
                ),
                "core": _document_payload(
                    window["core"],
                    start_ordinal=window["core_start_ordinal"],
                ),
                "context_after": _document_payload(
                    window["after"],
                    start_ordinal=window["after_start_ordinal"],
                ),
                "output": {
                    "summary": "简短内容概述",
                    "logic": ["按顺序说明内容如何推进"],
                    "speaker_style": "讲话方式概述",
                    "entities": [],
                    "search_queries": [],
                    "visual_questions": [],
                    "sections": [],
                },
            },
            profile_id=profile_id,
            max_tokens=5_000,
            timeout=180,
            call_stats=call_stats,
            call_id=f"window_{window_index}",
            trace_collector=trace_collector,
            completion_gateway=completion_gateway,
        )
        partials.append(partial)
        if raw_responses is not None:
            raw_responses.append(
                {
                    "stage": f"window_{window_index}",
                    "attempt": 1,
                    "response": partial,
                }
            )
    merged = _complete_understanding_json(
        "Merge these ordered window briefs into one compact document brief and continuous section plan. Do not correct transcript text. "
        "Write every reader-facing metadata value in concise Simplified Chinese. Keep proper names, search query strings, target terms, and "
        "transcript quotes in their original language. Return JSON only.",
        {"segment_count": len(segments), "window_briefs": partials},
        profile_id=profile_id,
        max_tokens=8_000,
        timeout=180,
        call_stats=call_stats,
        call_id="window_merge",
        trace_collector=trace_collector,
        completion_gateway=completion_gateway,
    )
    if raw_responses is not None:
        raw_responses.append({"stage": "window_merge", "attempt": 1, "response": merged})
    return merged


def _resolve_researched_entities(
    brief: dict,
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    evidence: list[dict],
    profile_id: str,
    resolution_trace_sink_factory: (
        Callable[[int], llm_runtime.TraceSink] | None
    ) = None,
    variant_trace_sink_factory: (
        Callable[[int], llm_runtime.TraceSink] | None
    ) = None,
    completion_gateway: (
        AsrDocumentUnderstandingCompletionGateway | None
    ) = None,
) -> list[dict]:
    candidates = [
        item
        for item in brief.get("entities", [])
        if isinstance(item, dict) and item.get("needs_research") and str(item.get("name") or "").strip()
    ]
    if not candidates or not evidence:
        return []
    raw = _complete_json_stable(
        "Resolve only proper-name candidates that the supplied research clearly identifies. "
        "Use each candidate's before/current/after sentences to decide whether it performs the same role and actions in the same workflow. "
        "ASR variants may look very different, so do not require spelling similarity; do not group them unless the sentence context supports it. "
        "A search result for a similarly named wrapper website does not prove the transcript switched away from the repeatedly used main product. "
        "Do not create a separate canonical entity for a one-off phonetic spelling when its surrounding workflow still matches that main product, "
        "unless direct visual text or unambiguous sentence context proves the separate entity. "
        "Evidence whose source_type is visual_text is high-confidence text read directly from a saved frame and is bound to its candidate_id; "
        "use it only for that matching candidate and never transfer it to another person, product or term. "
        "Every resolution must cite source ids whose title or snippet contains the canonical name. "
        "Write each resolution role in concise Simplified Chinese while keeping canonical names and transcript variants in their source spelling. "
        "Return JSON only.",
        {
            "document_summary": brief.get("summary"),
            "candidate_entities": candidates,
            "candidate_contexts": _candidate_entity_contexts(candidates, segments),
            "research_evidence": evidence,
            "output": {
                "resolutions": [
                    {
                        "canonical_name": "",
                        "variants": [],
                        "role": "",
                        "confidence": 0.0,
                        "evidence_source_ids": [],
                    }
                ]
            },
        },
        profile_id=profile_id,
        max_tokens=3_000,
        timeout=120,
        call_id="entity_resolution",
        trace_sink_factory=resolution_trace_sink_factory,
        completion_gateway=completion_gateway,
        contract_version="asr-entity-normalization-call-input-v1",
        behavior_version="asr-entity-normalization-v1",
    )
    evidence_ids = {str(item.get("source_id") or "") for item in evidence}
    resolutions = []
    for item in raw.get("resolutions", []) if isinstance(raw.get("resolutions"), list) else []:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical_name") or "").strip()
        cited = [str(value) for value in item.get("evidence_source_ids", []) if str(value) in evidence_ids]
        if not canonical or _confidence(item.get("confidence")) < 0.75:
            continue
        if not _evidence_supports_replacement(canonical, cited, evidence):
            continue
        variants = [str(value).strip() for value in item.get("variants", []) if str(value).strip()]
        if not _visual_evidence_supports_resolution(
            canonical,
            variants,
            cited,
            evidence,
        ):
            continue
        resolutions.append(
            {
                "canonical_name": canonical,
                "variants": variants[:20],
                "role": str(item.get("role") or "")[:300],
                "confidence": _confidence(item.get("confidence")),
                "evidence_source_ids": cited,
            }
        )
    resolutions = _drop_confusable_singleton_resolutions(
        resolutions[:12],
        segments=segments,
        evidence=evidence,
    )
    if resolutions:
        resolutions = _map_resolved_entity_variants(
            brief,
            resolutions=resolutions,
            candidate_contexts=_candidate_entity_contexts(candidates, segments),
            document=_document_payload(segments),
            profile_id=profile_id,
            trace_sink_factory=variant_trace_sink_factory,
            completion_gateway=completion_gateway,
        )
    return resolutions


def normalize_researched_entities(
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    document_summary: str,
    candidates: list[dict],
    evidence: list[dict],
    profile_id: str,
    locked_changes: list[dict] | None = None,
    resolution_trace_sink_factory: (
        Callable[[int], llm_runtime.TraceSink] | None
    ) = None,
    variant_trace_sink_factory: (
        Callable[[int], llm_runtime.TraceSink] | None
    ) = None,
    completion_gateway: (
        AsrDocumentUnderstandingCompletionGateway | None
    ) = None,
) -> tuple[
    list[VideoLocalizationTranscriptSegment],
    list[dict],
    list[dict],
    list[dict],
]:
    """Reuse the proven name-resolution algorithm through one public boundary."""

    brief = {
        "summary": document_summary,
        "logic": [],
        "entities": [
            {
                "name": term,
                "role": str(candidate.get("reason") or ""),
                "needs_research": True,
            }
            for candidate in candidates
            for term in candidate.get("target_terms", [])
            if entity_variant_safety.looks_like_proper_name_candidate(term)
        ],
    }
    resolutions = _resolve_researched_entities(
        brief,
        segments=segments,
        evidence=evidence,
        profile_id=profile_id,
        resolution_trace_sink_factory=resolution_trace_sink_factory,
        variant_trace_sink_factory=variant_trace_sink_factory,
        completion_gateway=completion_gateway,
    )
    updated, changes, warnings = _apply_resolved_entity_variants(
        segments,
        resolutions=resolutions,
        evidence=evidence,
        locked_changes=locked_changes,
    )
    return updated, resolutions, changes, warnings


def _map_resolved_entity_variants(
    brief: dict,
    *,
    resolutions: list[dict],
    candidate_contexts: list[dict],
    document: list[dict],
    profile_id: str,
    trace_sink_factory: Callable[[int], llm_runtime.TraceSink] | None = None,
    completion_gateway: (
        AsrDocumentUnderstandingCompletionGateway | None
    ) = None,
) -> list[dict]:
    raw = _complete_json_stable(
        "Canonical proper names have already been verified. For each canonical entity, identify candidate transcript spellings "
        "that refer to that same entity by comparing role, actions, surrounding sentences and continuous workflow. "
        "Also scan complete_document for same-entity ASR spellings omitted from candidate_contexts and include their exact text. "
        "ASR variants may look very different. A similarly named wrapper website in research is not a separate transcript entity when the "
        "continuous sentence context still describes the repeatedly used canonical product. "
        "Do not force genuinely unrelated people, platforms or supporting tools into a group. Return JSON only.",
        {
            "document_summary": brief.get("summary"),
            "document_logic": brief.get("logic"),
            "canonical_entities": resolutions,
            "candidate_contexts": candidate_contexts,
            "complete_document": document,
            "output": {"mappings": [{"canonical_name": "", "variants": [], "reason": ""}]},
        },
        profile_id=profile_id,
        max_tokens=3_000,
        timeout=120,
        call_id="entity_variant_mapping",
        trace_sink_factory=trace_sink_factory,
        completion_gateway=completion_gateway,
        contract_version="asr-entity-normalization-call-input-v1",
        behavior_version="asr-entity-normalization-v1",
    )
    candidate_names = [
        str(item.get("name") or "").strip()
        for item in candidate_contexts
        if str(item.get("name") or "").strip()
    ]
    document_text = " ".join(str(item.get("text") or "") for item in document)

    def projected_variant(value: object) -> str | None:
        variant = str(value).strip()
        if len(variant) < 2:
            return None
        for candidate_name in sorted(
            candidate_names,
            key=len,
            reverse=True,
        ):
            if variant.casefold() == candidate_name.casefold():
                return candidate_name
            if re.search(
                _variant_match_pattern(candidate_name),
                variant,
                flags=re.IGNORECASE,
            ):
                return candidate_name
        if re.search(
            _variant_match_pattern(variant),
            document_text,
            flags=re.IGNORECASE,
        ):
            return variant
        return None

    mappings = {
        str(item.get("canonical_name") or "").casefold(): list(
            dict.fromkeys(
                projected
                for value in item.get("variants", [])
                if (projected := projected_variant(value)) is not None
            )
        )
        for item in raw.get("mappings", [])
        if isinstance(item, dict) and isinstance(item.get("variants"), list)
    }
    local_mappings = _locally_map_unique_confusable_candidates(
        resolutions=resolutions,
        candidate_names=candidate_names,
        document_texts=[
            str(item.get("text") or "")
            for item in document
        ],
    )
    return [
        {
            **item,
            "variants": list(
                dict.fromkeys(
                    [
                        *[
                            value
                            for value in item.get("variants", [])
                            if entity_variant_safety.is_safe_proper_name_replacement(
                                item.get("canonical_name"),
                                value,
                            )
                        ],
                        *[
                            value
                            for value in mappings.get(
                                str(item.get("canonical_name") or "").casefold(),
                                [],
                            )
                            if entity_variant_safety.is_safe_proper_name_replacement(
                                item.get("canonical_name"),
                                value,
                            )
                        ],
                        *local_mappings.get(
                            str(item.get("canonical_name") or "").casefold(),
                            [],
                        ),
                    ]
                )
            )[:20],
        }
        for item in resolutions
    ]


def _locally_map_unique_confusable_candidates(
    *,
    resolutions: list[dict],
    candidate_names: list[str],
    document_texts: list[str],
) -> dict[str, list[str]]:
    """Fill one-off phonetic omissions only when one repeated canonical wins."""

    canonical_keys = {
        str(item.get("canonical_name") or "").strip().casefold()
        for item in resolutions
    }
    dominant = [
        item
        for item in resolutions
        if _entity_mention_count(
            _entity_transcript_aliases(
                str(item.get("canonical_name") or "")
            ),
            document_texts,
        )
        >= 2
    ]
    output: dict[str, list[str]] = {}
    for candidate in candidate_names:
        if (
            candidate.casefold() in canonical_keys
            or _entity_mention_count([candidate], document_texts) != 1
        ):
            continue
        scored: list[tuple[float, dict]] = []
        for item in dominant:
            canonical = str(item.get("canonical_name") or "").strip()
            if not entity_variant_safety.is_safe_proper_name_replacement(
                canonical,
                candidate,
            ):
                continue
            score = _entity_name_similarity(
                _entity_name_signature(candidate),
                _entity_name_signature(canonical),
            )
            threshold = (
                0.45
                if _matching_major_version(candidate, canonical)
                else 0.68
            )
            if score >= threshold:
                scored.append((score, item))
        scored.sort(key=lambda value: value[0], reverse=True)
        if not scored or (
            len(scored) > 1
            and scored[0][0] < scored[1][0] + 0.08
        ):
            continue
        canonical_key = str(
            scored[0][1].get("canonical_name") or ""
        ).casefold()
        output.setdefault(canonical_key, []).append(candidate)
    return output


def _matching_major_version(left: str, right: str) -> bool:
    left_numbers = re.findall(r"\d+(?:\.\d+)*", left)
    right_numbers = re.findall(r"\d+(?:\.\d+)*", right)
    if not left_numbers or not right_numbers:
        return False
    return (
        left_numbers[-1].split(".", 1)[0]
        == right_numbers[-1].split(".", 1)[0]
    )


def _drop_confusable_singleton_resolutions(
    resolutions: list[dict],
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    evidence: list[dict],
) -> list[dict]:
    """Drop weak duplicate canonicals so their transcript variant can join the dominant entity."""

    if len(resolutions) < 2:
        return resolutions
    segment_texts = [
        segment.corrected_text or segment.raw_text
        for segment in segments
    ]
    stats = [
        {
            "resolution": item,
            "canonical_mentions": _entity_mention_count(
                [str(item.get("canonical_name") or "")],
                segment_texts,
            ),
            "all_mentions": _entity_mention_count(
                [
                    *_entity_transcript_aliases(
                        str(item.get("canonical_name") or "")
                    ),
                    *[
                        str(value)
                        for value in item.get("variants", [])
                    ],
                ],
                segment_texts,
            ),
            "has_visual_evidence": _resolution_has_visual_evidence(
                item,
                evidence,
            ),
            "signature": _entity_name_signature(
                str(item.get("canonical_name") or "")
            ),
        }
        for item in resolutions
    ]
    retained: list[dict] = []
    for current in stats:
        resolution = current["resolution"]
        if (
            current["canonical_mentions"] > 0
            or current["all_mentions"] > 1
            or current["has_visual_evidence"]
        ):
            retained.append(resolution)
            continue
        confidence = _confidence(resolution.get("confidence"))
        duplicate_of_dominant = any(
            other is not current
            and other["all_mentions"] >= 2
            and _confidence(other["resolution"].get("confidence"))
            >= confidence + 0.05
            and _entity_name_similarity(
                current["signature"],
                other["signature"],
            )
            >= 0.68
            for other in stats
        )
        if not duplicate_of_dominant:
            retained.append(resolution)
    return retained


def _entity_transcript_aliases(value: str) -> list[str]:
    canonical = value.strip()
    aliases = [canonical] if canonical else []
    versionless = re.sub(
        r"(?:\s+v?\d+(?:\.\d+)*)$",
        "",
        canonical,
        flags=re.IGNORECASE,
    ).strip()
    if len(versionless) >= 4 and versionless.casefold() != canonical.casefold():
        aliases.append(versionless)
    return aliases


def _entity_mention_count(
    values: list[str],
    segment_texts: list[str],
) -> int:
    names = [
        value.strip()
        for value in values
        if value.strip()
    ]
    return sum(
        1
        for text in segment_texts
        if any(
            re.search(
                _variant_match_pattern(name),
                text,
                flags=re.IGNORECASE,
            )
            for name in names
        )
    )


def _resolution_has_visual_evidence(
    resolution: dict,
    evidence: list[dict],
) -> bool:
    cited = {
        str(value)
        for value in resolution.get("evidence_source_ids", [])
    }
    return any(
        str(item.get("source_id") or "") in cited
        and item.get("source_type") == "visual_text"
        for item in evidence
    )


def _entity_name_signature(value: str) -> str:
    tokens = [
        token.casefold()
        for token in re.findall(r"[A-Za-z]+|\d+(?:\.\d+)*", value)
    ]
    while tokens and (
        tokens[-1] == "ai"
        or re.fullmatch(r"\d+(?:\.\d+)*", tokens[-1])
    ):
        tokens.pop()
    return "".join(tokens)


def _entity_name_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _candidate_entity_contexts(
    candidates: list[dict],
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[dict]:
    contexts = []
    for candidate in candidates:
        name = str(candidate.get("name") or "").strip()
        matches = []
        for index, segment in enumerate(segments):
            text = segment.corrected_text or segment.raw_text
            if not re.search(re.escape(name), text, flags=re.IGNORECASE):
                continue
            matches.append(
                {
                    "segment_id": segment.segment_id,
                    "before": (segments[index - 1].corrected_text or segments[index - 1].raw_text) if index else "",
                    "text": text,
                    "after": (
                        segments[index + 1].corrected_text or segments[index + 1].raw_text
                        if index + 1 < len(segments)
                        else ""
                    ),
                }
            )
            if len(matches) >= 3:
                break
        contexts.append({"name": name, "role": candidate.get("role"), "occurrences": matches})
    return contexts


def _apply_resolved_entity_variants(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    resolutions: list[dict],
    evidence: list[dict],
    locked_changes: list[dict] | None = None,
) -> tuple[list[VideoLocalizationTranscriptSegment], list[dict], list[dict]]:
    decisions = []
    planned_targets: set[tuple[str, str]] = set()
    for segment in segments:
        current = segment.corrected_text or segment.raw_text
        for resolution in resolutions:
            canonical = str(resolution.get("canonical_name") or "").strip()
            cited = [str(value) for value in resolution.get("evidence_source_ids", [])]
            for variant in sorted(resolution.get("variants") or [], key=lambda value: len(str(value)), reverse=True):
                variant = str(variant).strip()
                if not entity_variant_safety.is_safe_proper_name_replacement(
                    canonical,
                    variant,
                ):
                    continue
                match = re.search(_variant_match_pattern(variant), current, flags=re.IGNORECASE)
                if not canonical or not variant or match is None or variant.casefold() == canonical.casefold():
                    continue
                replacement = _canonical_replacement_preserving_version(
                    canonical,
                    match.group(0),
                )
                if replacement.casefold() == match.group(0).casefold():
                    continue
                decisions.append(
                    {
                        "segment_id": segment.segment_id,
                        "excerpt": match.group(0),
                        "accept": True,
                        "replacement": replacement,
                        "reason": "查证资料或画面文字已确认规范名称，全文上下文也确认这是同一实体的识别变体。",
                        "confidence": resolution.get("confidence"),
                        "evidence_source_ids": cited,
                    }
                )
                planned_targets.add(
                    (segment.segment_id, match.group(0).casefold())
                )
                break
    for left, right in zip(segments, segments[1:]):
        left_text = left.corrected_text or left.raw_text
        right_text = right.corrected_text or right.raw_text
        combined = f"{left_text}\n{right_text}"
        boundary = len(left_text)
        matched_pair = False
        for resolution in resolutions:
            canonical = str(
                resolution.get("canonical_name") or ""
            ).strip()
            cited = [
                str(value)
                for value in resolution.get(
                    "evidence_source_ids",
                    [],
                )
            ]
            for raw_variant in sorted(
                resolution.get("variants") or [],
                key=lambda value: len(str(value)),
                reverse=True,
            ):
                variant = str(raw_variant).strip()
                if (
                    not canonical
                    or not variant
                    or variant.casefold() == canonical.casefold()
                    or not entity_variant_safety
                    .is_safe_proper_name_replacement(
                        canonical,
                        variant,
                    )
                ):
                    continue
                match = re.search(
                    _variant_match_pattern(variant),
                    combined,
                    flags=re.IGNORECASE,
                )
                if (
                    match is None
                    or not (match.start() < boundary < match.end())
                ):
                    continue
                left_excerpt = combined[match.start() : boundary]
                right_excerpt = combined[boundary + 1 : match.end()]
                replacement = _canonical_prefix_before_version_suffix(
                    canonical,
                    right_excerpt,
                )
                target = (left.segment_id, left_excerpt.casefold())
                if (
                    replacement is None
                    or not left_excerpt.strip()
                    or target in planned_targets
                ):
                    continue
                decisions.append(
                    {
                        "segment_id": left.segment_id,
                        "excerpt": left_excerpt,
                        "accept": True,
                        "replacement": replacement,
                        "reason": (
                            "查证资料或画面文字已确认规范名称；"
                            "该名称的版本号被 ASR 拆到相邻片段，"
                            "本次只修正名称并保留两段中的全部数字。"
                        ),
                        "confidence": resolution.get("confidence"),
                        "evidence_source_ids": cited,
                    }
                )
                planned_targets.add(target)
                matched_pair = True
                break
            if matched_pair:
                break
    return _apply_decisions(
        segments,
        decisions,
        evidence=evidence,
        locked_changes=locked_changes,
    )


def _variant_match_pattern(variant: str) -> str:
    parts = [re.escape(item) for item in re.split(r"[-\s]+", variant.strip()) if item]
    pattern = r"[-\s]*".join(parts)
    pattern = pattern.replace(r"\.", r"\s*\.\s*")
    if re.match(r"[A-Za-z0-9_]", variant):
        pattern = rf"(?<!\w){pattern}"
    if re.search(r"[A-Za-z0-9_]$", variant):
        pattern = rf"{pattern}(?!\w)"
    return pattern


def _canonical_prefix_before_version_suffix(
    canonical: str,
    suffix: str,
) -> str | None:
    """Keep a numeric version suffix in the next ASR segment."""

    if not re.fullmatch(
        r"\s*v?\d+(?:\s*\.\s*\d+)*\s*",
        suffix,
        flags=re.IGNORECASE,
    ):
        return None
    suffix_chars = [
        character
        for character in suffix
        if not character.isspace()
    ]
    canonical_index = len(canonical) - 1
    for character in reversed(suffix_chars):
        while (
            canonical_index >= 0
            and canonical[canonical_index].isspace()
        ):
            canonical_index -= 1
        if (
            canonical_index < 0
            or canonical[canonical_index].casefold()
            != character.casefold()
        ):
            return None
        canonical_index -= 1
    prefix = canonical[: canonical_index + 1].rstrip()
    return prefix or None


def _canonical_replacement_preserving_version(canonical: str, variant: str) -> str:
    canonical_base = re.sub(r"\s+v?\d+(?:\s*\.\s*\d+)*\s*$", "", canonical, flags=re.IGNORECASE).strip()
    version = re.search(r"\s+(v?\d+(?:\s*\.\s*\d+)*)\s*$", variant, flags=re.IGNORECASE)
    return f"{canonical_base} {version.group(1)}" if version else canonical_base


def _apply_decisions(
    segments: list[VideoLocalizationTranscriptSegment],
    decisions: list[dict],
    *,
    evidence: list[dict],
    locked_changes: list[dict] | None = None,
) -> tuple[list[VideoLocalizationTranscriptSegment], list[dict], list[dict]]:
    output, changes, warnings = review_decisions_domain.apply_decisions(
        segments,
        decisions,
        evidence=evidence,
        locked_changes=locked_changes,
    )
    return (
        output,
        changes,
        [
            {
                "code": "needs_confirmation",
                "segment_id": str(item.get("segment_id") or ""),
                "excerpt": str(item.get("excerpt") or ""),
                "message": str(item.get("message") or ""),
            }
            for item in warnings
        ],
    )


def _normalize_brief(raw: dict, *, segment_count: int) -> dict:
    sections = _normalize_sections(raw.get("sections"), segment_count=segment_count, allow_empty=True)
    entities = [item for item in raw.get("entities", []) if isinstance(item, dict)][:40]
    queries = []
    for item in raw.get("search_queries", []) if isinstance(raw.get("search_queries"), list) else []:
        if not isinstance(item, dict) or not str(item.get("query") or "").strip():
            continue
        category = str(item.get("category") or "background").strip()
        if category not in {"proper_noun", "background", "culture", "persona"}:
            category = "background"
        queries.append(
            {
                "query": str(item.get("query") or "").strip()[:240],
                "category": category,
                "reason": str(item.get("reason") or "").strip()[:800],
                "target_terms": [
                    str(value).strip()
                    for value in (
                        item.get("target_terms")
                        if isinstance(item.get("target_terms"), list)
                        else []
                    )
                    if str(value).strip()
                ][: research_limits.MAX_RESEARCH_TARGET_TERMS],
            }
        )
    visual_questions = []
    for item in (
        raw.get("visual_questions", [])
        if isinstance(raw.get("visual_questions"), list)
        else []
    ):
        if not isinstance(item, dict):
            continue
        try:
            start_segment = max(
                1,
                min(segment_count, int(item.get("start_segment") or 1)),
            )
            end_segment = max(
                start_segment,
                min(
                    segment_count,
                    int(item.get("end_segment") or start_segment),
                ),
            )
        except (TypeError, ValueError):
            continue
        question = str(item.get("question") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not question or not reason:
            continue
        kind = str(item.get("kind") or "scene_context").strip()
        if kind not in {"visible_text", "chart", "object", "scene_context"}:
            kind = "scene_context"
        frame_strategy = str(
            item.get("frame_strategy") or "nearby"
        ).strip()
        if frame_strategy not in {"nearby", "look_ahead"}:
            frame_strategy = "nearby"
        visual_questions.append(
            {
                "start_segment": start_segment,
                "end_segment": end_segment,
                "kind": kind,
                "reason": reason[:800],
                "question": question[:800],
                "frame_strategy": frame_strategy,
            }
        )
    return {
        "content_kind": (
            str(raw.get("content_kind") or "unknown").strip()
            if str(raw.get("content_kind") or "unknown").strip()
            in {
                "film_or_drama",
                "interview",
                "tutorial",
                "presentation",
                "conversation",
                "other",
                "unknown",
            }
            else "unknown"
        ),
        "summary": _mask_unverified_names(str(raw.get("summary") or "已理解完整转写内容。")[:1_000], entities),
        "logic": [
            _mask_unverified_names(str(item).strip(), entities) for item in raw.get("logic", []) if str(item).strip()
        ][:12],
        "speaker_style": str(raw.get("speaker_style") or "")[:500],
        "language_notes": [
            str(item).strip()[:500]
            for item in (
                raw.get("language_notes")
                if isinstance(raw.get("language_notes"), list)
                else []
            )
            if str(item).strip()
        ][:12],
        "entities": entities,
        "search_queries": queries,
        "visual_questions": visual_questions[:12],
        "sections": sections,
    }


def _mask_unverified_names(value: str, entities: list[dict]) -> str:
    output = value
    names = sorted(
        {
            str(item.get("name") or "").strip()
            for item in entities
            if item.get("needs_research") and len(str(item.get("name") or "").strip()) >= 3
        },
        key=len,
        reverse=True,
    )
    for name in names:
        output = re.sub(re.escape(name), "[名称待核实]", output, flags=re.IGNORECASE)
    return re.sub(r"(?:\[名称待核实\]\s*){2,}", "[名称待核实]", output)


def _brief_is_usable(brief: dict) -> bool:
    summary = str(brief.get("summary") or "").strip()
    logic = brief.get("logic")
    speaker_style = str(brief.get("speaker_style") or "").strip()
    sections = brief.get("sections")
    return (
        bool(summary)
        and summary != "已理解完整转写内容。"
        and isinstance(logic, list)
        and bool(logic)
        and bool(speaker_style)
        and isinstance(sections, list)
        and bool(sections)
        and _sections_have_distinct_focus(sections)
        and _brief_uses_simplified_chinese(brief)
    )


def _brief_uses_simplified_chinese(brief: dict) -> bool:
    reader_values = [
        str(brief.get("summary") or ""),
        str(brief.get("speaker_style") or ""),
        *[str(item) for item in brief.get("logic") or []],
        *[
            str(item.get("role") or "")
            for item in brief.get("entities") or []
            if isinstance(item, dict) and item.get("role")
        ],
        *[
            str(item.get("reason") or "")
            for item in brief.get("search_queries") or []
            if isinstance(item, dict) and item.get("reason")
        ],
        *[
            value
            for item in brief.get("visual_questions") or []
            if isinstance(item, dict)
            for value in [
                str(item.get("reason") or ""),
                str(item.get("question") or ""),
            ]
            if value
        ],
        *[
            value
            for item in brief.get("sections") or []
            if isinstance(item, dict)
            for value in [
                str(item.get("role") or ""),
                *[str(focus) for focus in item.get("focus") or []],
            ]
        ],
    ]
    return bool(reader_values) and all(
        re.search(r"[\u3400-\u9fff]", value)
        for value in reader_values
        if value.strip()
    )


def _normalize_sections(raw: object, *, segment_count: int, allow_empty: bool) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    sections = []
    expected = 1
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_segment") or 0)
            end = int(item.get("end_segment") or 0)
        except (TypeError, ValueError):
            continue
        if start != expected or end < start or end > segment_count:
            continue
        raw_focus = item.get("focus", [])
        focus_values = [raw_focus] if isinstance(raw_focus, str) else raw_focus if isinstance(raw_focus, list) else []
        sections.append(
            {
                "id": str(item.get("id") or f"S{index}"),
                "start_segment": start,
                "end_segment": end,
                "role": str(item.get("role") or "").strip()[:300],
                "focus": [str(value).strip() for value in focus_values if str(value).strip()][:6],
            }
        )
        expected = end + 1
    if sections and expected == segment_count + 1:
        return sections
    if allow_empty:
        return []
    return _fallback_sections(segment_count, _initial_section_count(segment_count))


def _sections_have_distinct_focus(sections: list[dict]) -> bool:
    if any(not str(item.get("role") or "").strip() or not item.get("focus") for item in sections):
        return False
    signatures = {
        (
            str(item.get("role") or "").strip().casefold(),
            tuple(str(value).strip().casefold() for value in item.get("focus") or []),
        )
        for item in sections
    }
    return len(sections) == 1 or len(signatures) == len(sections)


def _fallback_sections(segment_count: int, count: int) -> list[dict]:
    count = max(1, min(count, segment_count))
    sections = []
    for index in range(count):
        if count == 1:
            role = "完整内容"
            focus = ["结合全文背景核对错字、名称、数字、指代和句意"]
        elif index == 0:
            role = "开场与主题建立"
            focus = ["核对主题、人物、地点和首次出现的关键名词"]
        elif index == count - 1:
            role = "收束与结论"
            focus = ["核对结论、行动呼吁以及与前文的呼应"]
        else:
            role = f"主体推进 {index}"
            focus = [f"核对第 {index} 个主体区块的事实、步骤、因果和上下文衔接"]
        sections.append(
            {
                "id": f"S{index + 1}",
                "start_segment": math.floor(index * segment_count / count) + 1,
                "end_segment": math.floor((index + 1) * segment_count / count),
                "role": role,
                "focus": focus,
            }
        )
    return sections


def _large_windows(segments: list[VideoLocalizationTranscriptSegment]) -> list[dict]:
    windows = []
    start = 0
    while start < len(segments):
        end = start
        size = 0
        while end < len(segments):
            text = segments[end].corrected_text or segments[end].raw_text
            if end > start and size + len(text) > LARGE_WINDOW_MAX_CHARS:
                break
            size += len(text)
            end += 1
        windows.append(
            {
                "before": segments[max(0, start - REVIEW_OVERLAP_SEGMENTS) : start],
                "core": segments[start:end],
                "after": segments[end : min(len(segments), end + REVIEW_OVERLAP_SEGMENTS)],
                "before_start_ordinal": max(0, start - REVIEW_OVERLAP_SEGMENTS) + 1,
                "core_start_ordinal": start + 1,
                "after_start_ordinal": end + 1,
            }
        )
        start = end
    return windows


def _document_payload(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    start_ordinal: int = 1,
) -> list[dict]:
    return [
        {
            "ordinal": start_ordinal + index,
            "segment_id": segment.segment_id,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "text": (segment.corrected_text or segment.raw_text).strip(),
            "speaker_cluster_id": segment.speaker_cluster_id,
            "speaker_confidence": segment.speaker_confidence,
            "has_speaker_overlap": segment.has_speaker_overlap,
        }
        for index, segment in enumerate(segments)
        if (segment.corrected_text or segment.raw_text).strip()
    ]


def _repeated_terms(segments: list[VideoLocalizationTranscriptSegment]) -> list[dict]:
    counts = Counter(
        token
        for segment in segments
        for token in re.findall(r"\b[A-Z][A-Za-z0-9-]{3,}\b", segment.corrected_text or segment.raw_text)
    )
    return [{"term": term, "occurrences": count} for term, count in counts.most_common(30) if count >= 2]


def _looks_like_entity_change(excerpt: str, replacement: str, reason: str) -> bool:
    return review_decisions_domain.looks_like_entity_change(
        excerpt,
        replacement,
        reason,
    )


def _evidence_supports_replacement(replacement: str, cited: list[str], evidence: list[dict]) -> bool:
    return review_decisions_domain.evidence_supports_replacement(
        replacement,
        cited,
        evidence,
    )


def _visual_evidence_supports_resolution(
    canonical: str,
    variants: list[str],
    cited: list[str],
    evidence: list[dict],
) -> bool:
    """Keep candidate-bound visual text from resolving another entity."""

    cited_set = set(cited)
    visual_items = [
        item
        for item in evidence
        if str(item.get("source_id") or "") in cited_set
        and item.get("source_type") == "visual_text"
    ]
    if not visual_items:
        return True
    canonical_key = canonical.strip().casefold()
    if any(
        str(item.get("canonical_text") or "").strip().casefold()
        != canonical_key
        for item in visual_items
    ):
        return False
    target_keys = {
        str(value).strip().casefold()
        for item in visual_items
        for value in item.get("candidate_target_terms", [])
        if str(value).strip()
    }
    variant_keys = {
        value.strip().casefold()
        for value in variants
        if value.strip()
    }
    return bool(target_keys & variant_keys)


def _dedupe_issues(items: list[dict]) -> list[dict]:
    output = []
    seen = set()
    for item in items:
        key = (item.get("segment_id"), item.get("current_excerpt"), item.get("replacement"), item.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _dedupe_warnings(items: list[dict]) -> list[dict]:
    output = []
    seen = set()
    for item in items:
        key = (item.get("segment_id"), item.get("excerpt"), item.get("message"))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _active_warnings(
    items: list[dict],
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[dict]:
    current_by_id = {segment.segment_id: segment.corrected_text or segment.raw_text for segment in segments}
    output = []
    for item in _dedupe_warnings(items):
        message = f"{item.get('message') or ''} {item.get('reason') or ''}"
        if re.search(r"(?:当前|这句|该处|名称).{0,10}(?:正确|已修正|已解决|无需修改|不需要修改)", message):
            continue
        segment_id = str(item.get("segment_id") or "").strip()
        excerpt = str(item.get("excerpt") or "").strip()
        if segment_id and excerpt and segment_id in current_by_id:
            current_key = _display_text_key(current_by_id[segment_id])
            excerpt_key = _display_text_key(excerpt)
            if excerpt_key and excerpt_key not in current_key:
                continue
        output.append(item)
    return output


def _recheck_step_summary(
    recheck: dict,
    *,
    applied_count: int,
) -> str:
    prefix = f"已完成本地收尾检查，本轮 {applied_count} 处修改已经合并。"
    if recheck.get("passed"):
        return f"{prefix}没有发现必须继续处理的问题。"
    if recheck.get("failed"):
        return (
            f"{prefix}"
            f"{str(recheck.get('summary') or '全文复核没有完成。')}"
        )
    if not recheck.get("continue_review", True):
        return f"{prefix}没有给出需要继续处理的区块，本次结束自动复查。"
    return f"{prefix}剩余位置已保留原文并标记复听。"


def _display_text_key(value: object) -> str:
    return re.sub(r"[^\w]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _limit_user_warnings(items: list[dict], limit: int = 8) -> list[dict]:
    if len(items) <= limit:
        return items
    hidden = len(items) - limit
    return [
        *items[:limit],
        _uncertain_warning("", "", f"另外还有 {hidden} 处同类问题，字幕没有改，请结合原音确认。"),
    ]


def _initial_section_count(segment_count: int) -> int:
    if segment_count <= 24:
        return 1
    if segment_count <= 80:
        return 4
    if segment_count <= 220:
        return 5
    return 7


def _resolve_profile(profile_id: str | None):
    try:
        return llm_runtime.resolve_profile(profile_id)
    except llm_runtime.LlmRuntimeError:
        return None


def _complete_understanding_json(
    prompt: str,
    payload: dict,
    *,
    profile_id: str,
    max_tokens: int,
    timeout: float,
    call_stats: dict[str, int] | None,
    call_id: str = "understand_document",
    trace_collector: llm_observability.AsrLlmTraceCollector | None = None,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None) = None,
) -> dict:
    if call_stats is not None:
        call_stats["logical"] = call_stats.get("logical", 0) + 1

    def count_request() -> None:
        if call_stats is not None:
            call_stats["requests"] = call_stats.get("requests", 0) + 1

    return _complete_json_stable(
        prompt,
        payload,
        profile_id=profile_id,
        max_tokens=max_tokens,
        timeout=timeout,
        on_attempt=count_request,
        call_id=f"understand_document:{call_id}",
        trace_sink_factory=(
            (
                lambda attempt: trace_collector.sink(
                    call_id=(f"understand_document:{call_id}:attempt_{attempt}"),
                    purpose="document_understanding",
                    round_index=1,
                )
            )
            if trace_collector is not None
            else None
        ),
        completion_gateway=completion_gateway,
    )


def _complete_json_stable(
    prompt: str,
    payload: dict,
    *,
    profile_id: str,
    max_tokens: int,
    timeout: float,
    on_attempt: Callable[[], None] | None = None,
    call_id: str = "completion",
    trace_sink_factory: Callable[[int], llm_runtime.TraceSink] | None = None,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None) = None,
    contract_version: str = "asr-document-understanding-call-input-v1",
    behavior_version: str = PROMPT_VERSION,
) -> dict:
    try:
        if on_attempt:
            on_attempt()
        raw = _complete_document_understanding_attempt(
            AsrDocumentUnderstandingCompletionRequest(
                contract_version=contract_version,
                behavior_version=behavior_version,
                call_id=call_id,
                attempt=1,
                system_prompt=prompt,
                user_payload=payload,
                profile_id=profile_id,
                max_tokens=max_tokens,
                timeout=timeout,
                disable_reasoning=False,
                trace_sink=(trace_sink_factory(1) if trace_sink_factory is not None else None),
            ),
            completion_gateway=completion_gateway,
        )
    except llm_runtime.LlmRuntimeError as exc:
        if exc.code not in RETRYABLE_OUTPUT_CODES:
            raise
        if on_attempt:
            on_attempt()
        raw = _complete_document_understanding_attempt(
            AsrDocumentUnderstandingCompletionRequest(
                contract_version=contract_version,
                behavior_version=behavior_version,
                call_id=call_id,
                attempt=2,
                system_prompt=prompt,
                user_payload=payload,
                profile_id=profile_id,
                max_tokens=max(max_tokens, 12_000),
                timeout=timeout,
                disable_reasoning=True,
                trace_sink=(trace_sink_factory(2) if trace_sink_factory is not None else None),
            ),
            completion_gateway=completion_gateway,
        )
    if not isinstance(raw, dict):
        raise llm_runtime.LlmRuntimeError("语言模型没有返回对象", code="llm_json_not_object", status_code=502)
    return raw


def _complete_document_understanding_attempt(
    request: AsrDocumentUnderstandingCompletionRequest,
    *,
    completion_gateway: (AsrDocumentUnderstandingCompletionGateway | None),
) -> dict:
    if completion_gateway is not None:
        return completion_gateway(request)
    result = llm_runtime.complete_json(
        system_prompt=request.system_prompt,
        user_payload=request.user_payload,
        profile_id=request.profile_id,
        max_tokens=request.max_tokens,
        timeout=request.timeout,
        disable_reasoning=request.disable_reasoning,
        trace_sink=request.trace_sink,
    )
    if not isinstance(result, dict):
        raise llm_runtime.LlmRuntimeError(
            "语言模型返回的 JSON 不是对象",
            code="llm_json_not_object",
            status_code=502,
        )
    return result



def _empty_run(
    segments: list[VideoLocalizationTranscriptSegment], recorder: _Recorder, started_at: float
) -> AsrReviewRun:
    return _simple_run(segments, recorder, started_at, status="skipped", message="没有可供复核的 ASR 文本。")


def _unconfigured_run(
    segments: list[VideoLocalizationTranscriptSegment],
    recorder: _Recorder,
    started_at: float,
    *,
    changes: list[dict] | None = None,
) -> AsrReviewRun:
    message = "没有可用的 ASR 复核模型，已保留原始转写。"
    if changes:
        message = f"没有可用的 ASR 复核模型；已应用 {len(changes)} 条项目术语，其他内容保留原始转写。"
    return _simple_run(
        segments,
        recorder,
        started_at,
        status="not_configured",
        message=message,
        changes=changes,
    )


def _simple_run(
    segments: list[VideoLocalizationTranscriptSegment],
    recorder: _Recorder,
    started_at: float,
    *,
    status: str,
    message: str,
    changes: list[dict] | None = None,
) -> AsrReviewRun:
    changes = list(changes or [])
    result_status = "warning" if status == "not_configured" else status
    step_result = {
        "label": "理解全文并规划复查",
        "order": 30,
        "status": result_status,
        "purpose": "检查整份转写里的听写错误和名称写法；没有可用模型时保留原文，不会猜着修改。",
        "summary": message,
        "metrics": [{"label": "已按术语表修改", "value": str(len(changes))}],
        "sections": ([{"title": "实际修改", "items": [_change_item(item) for item in changes]}] if changes else []),
        "notes": [],
    }
    recorder.results["understand_document"] = step_result
    recorder.definitions.append({"id": "understand_document", "label": "理解全文并规划复查", "order": 30})
    if recorder.on_progress:
        recorder.on_progress(0.30, "flow:understand_document|理解全文并规划复查")
    if recorder.on_report:
        recorder.on_report("understand_document", step_result)
    return AsrReviewRun(
        segments=segments,
        research=VideoLocalizationResearchState(
            status="not_configured" if status == "not_configured" else "not_needed"
        ),
        profile_id=None,
        model_id=None,
        report={
            "status": status,
            "prompt_version": PROMPT_VERSION,
            "assessment_rounds": 0,
            "repair_rounds": 0,
            "total_repairs": len(changes),
            "changes": changes,
            "warnings": [],
            "duration_ms": _elapsed_ms(started_at),
            "step_result": step_result,
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
        stage_timings={},
        review_meta={
            "status": status,
            "profile_id": None,
            "model_id": None,
            "error": message if status == "not_configured" else None,
            "quality_flags": ["asr_flow_deferred"] if status == "not_configured" else [],
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
    )


def _degraded_run(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    started_at: float,
    error: llm_runtime.LlmRuntimeError,
    changes: list[dict],
    on_progress: ProgressCallback | None,
    on_report: ReportCallback | None,
    recorder: _Recorder,
    stage_timings: dict[str, dict],
) -> AsrReviewRun:
    message = "语言复核暂时没有完成，已保留原始 ASR 并继续生成时间轴。"
    error_detail = {
        "code": error.code,
        "message": str(error),
        "status_code": error.status_code,
        "action": "continue_with_original_asr",
    }
    research = recorder.research or VideoLocalizationResearchState(
        status="failed" if "research" in recorder.results else "disabled",
        error=str(error) if "research" in recorder.results else None,
        reason="前序步骤中断，未开始资料查证。" if "research" not in recorder.results else "",
    )
    # Execution evidence survives degradation; it does not approve any edits.
    # Close the interrupted step so a completed operation cannot look running.
    for step_id, previous in list(recorder.results.items()):
        if previous.get("status") != "running":
            continue
        interrupted = {
            **previous,
            "status": "warning",
            "summary": "本步未完成，已保留前面步骤的执行记录；字幕使用原始听写。",
            "error_detail": error_detail,
        }
        recorder.results[step_id] = interrupted
        if on_report:
            on_report(step_id, interrupted)
    step_result = {
        "label": "ASR 语言复核",
        "order": 30,
        "status": "warning",
        "purpose": "检查整份转写里的听写错误和名称写法；本次检查未完成，所以未确认的内容保持原样。",
        "summary": message,
        "metrics": [
            {"label": "处理方式", "value": "未确认内容保持原样"},
            {"label": "已按术语表修改", "value": str(len(changes))},
        ],
        "sections": ([{"title": "实际修改", "items": [_change_item(item) for item in changes]}] if changes else []),
        "notes": [str(error)],
        "error_detail": error_detail,
    }
    recorder.results["asr_review_deferred"] = step_result
    recorder.definitions.append({"id": "asr_review_deferred", "label": "ASR 语言复核", "order": 30})
    if on_progress:
        on_progress(0.54, "flow:asr_review_deferred|ASR 语言复核暂缓")
    if on_report:
        on_report("asr_review_deferred", step_result)
    return AsrReviewRun(
        segments=copy.deepcopy(segments),
        research=research,
        profile_id=None,
        model_id=None,
        report={
            "status": "degraded",
            "prompt_version": PROMPT_VERSION,
            "assessment_rounds": 0,
            "repair_rounds": 0,
            "total_repairs": len(changes),
            "changes": changes,
            "warnings": [_uncertain_warning("", "", message)],
            "duration_ms": _elapsed_ms(started_at),
            "error_detail": error_detail,
            "step_result": step_result,
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
        stage_timings={
            **stage_timings,
            "asr_review_deferred": {
                "duration_ms": _elapsed_ms(started_at),
                "status": "degraded",
                "error_detail": error_detail,
            }
        },
        review_meta={
            "status": "partial",
            "profile_id": None,
            "model_id": None,
            "error": str(error),
            "quality_flags": ["asr_flow_deferred"],
            "task_flow": recorder.definitions,
            "task_step_results": recorder.results,
        },
    )


def _publish_segments_changed(
    callback: SegmentsChangedCallback | None,
    step_id: str,
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    changes: list[dict],
) -> None:
    if callback is None or not changes:
        return
    callback(step_id, copy.deepcopy(segments))


def _apply_explicit_glossary(
    segments: list[VideoLocalizationTranscriptSegment],
    glossary: list[VideoLocalizationGlossaryEntry],
) -> tuple[list[VideoLocalizationTranscriptSegment], list[dict]]:
    entries = sorted(
        (
            (item.source_text.strip(), (item.corrected_source_text or "").strip(), item.glossary_id)
            for item in glossary
            if item.source_text.strip() and (item.corrected_source_text or "").strip()
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if not entries:
        return copy.deepcopy(segments), []

    output = copy.deepcopy(segments)
    changes: list[dict] = []
    for index, segment in enumerate(output):
        before = segment.corrected_text or segment.raw_text
        after = before
        applied_ids: list[str] = []
        for source_text, replacement_text, glossary_id in entries:
            pattern = re.escape(source_text)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'-]*[A-Za-z0-9]", source_text):
                pattern = rf"(?<!\w){pattern}(?!\w)"
            replaced, count = re.subn(pattern, replacement_text, after, flags=re.IGNORECASE)
            if count:
                after = replaced
                applied_ids.append(glossary_id)
        if after == before:
            continue
        output[index] = segment.model_copy(
            update={
                "corrected_text": after,
                "review_confidence": 1.0,
                "review_flags": sorted(set([*segment.review_flags, "glossary_corrected"])),
            }
        )
        changes.append(
            {
                "segment_id": segment.segment_id,
                "before": before,
                "after": after,
                "reason": f"项目术语表：{', '.join(applied_ids)}",
                "confidence": 1.0,
                "evidence_source_ids": [],
            }
        )
    return output, changes


def _serializable_brief(brief: dict) -> dict:
    return {key: value for key, value in brief.items() if key != "search_queries"} | {
        "search_queries": [item.model_dump(mode="json") for item in brief.get("search_queries", [])]
    }


def _section_item(section: dict) -> dict:
    focus = "；".join(section.get("focus") or []) or "是否有听错、错字或意思不通"
    focus = focus.rstrip("。.!！?？；;，, ")
    return {
        "title": f"{section['id']}：{section.get('role') or '连续内容'}",
        "text": f"重点看：{focus}。",
        "meta": f"听写片段 {section['start_segment']} - {section['end_segment']}",
        "facts": [],
        "links": [],
        "tone": "neutral",
    }


def _issue_item(issue: dict) -> dict:
    replacement = str(issue.get("replacement") or "").strip()
    return {
        "title": f"可能听错：{issue.get('current_excerpt') or '这一处'}",
        "text": str(issue.get("reason") or "模型发现这里可能有听写问题。"),
        "meta": str(issue.get("segment_id") or ""),
        "facts": [
            *([{"label": "建议改成", "value": replacement}] if replacement else []),
            {"label": "把握度", "value": f"{_confidence(issue.get('confidence')):.0%}"},
        ],
        "links": [],
        "tone": "warning",
    }


def _brief_item(brief: dict) -> dict:
    entities = [str(item.get("name") or "").strip() for item in brief.get("entities", []) if isinstance(item, dict)]
    return {
        "title": "这段内容讲什么",
        "text": str(brief.get("summary") or ""),
        "facts": [
            {"label": "内容顺序", "value": "；".join(brief.get("logic") or [])[:500]},
            {"label": "说话方式", "value": str(brief.get("speaker_style") or "")[:300]},
            {"label": "待核对名称", "value": "、".join(filter(None, entities))[:500]},
        ],
        "links": [],
        "tone": "neutral",
    }


def _research_query_item(query: web_research.PlannedQuery) -> dict:
    return {
        "title": f"要查：{query.query}",
        "text": query.reason or "核对全文中的名称或背景疑点。",
        "facts": ([{"label": "原文写法", "value": "、".join(query.target_terms)}] if query.target_terms else []),
        "links": [],
        "tone": "neutral",
    }


def _research_evidence_item(item: dict) -> dict:
    return {
        "title": str(item.get("title") or item.get("source_id") or "搜索资料"),
        "text": str(item.get("snippet") or "")[:500],
        "meta": "可用资料",
        "facts": [],
        "links": ([{"title": "打开来源", "url": str(item.get("url"))}] if item.get("url") else []),
        "tone": "positive",
    }


def _resolved_entity_item(item: dict) -> dict:
    return {
        "title": f"已核实：{item.get('canonical_name')}",
        "text": str(item.get("role") or "根据公开资料核对名称。"),
        "facts": [
            {"label": "转写里还出现过", "value": "、".join(item.get("variants") or [])},
            {"label": "把握度", "value": f"{_confidence(item.get('confidence')):.0%}"},
        ],
        "links": [],
        "tone": "positive",
    }


def _change_item(change: dict) -> dict:
    return {
        "title": "已修正一处听写",
        "text": str(change.get("reason") or "已根据原音和上下文改正。"),
        "before": str(change.get("before") or ""),
        "after": str(change.get("after") or ""),
        "before_label": "修改前",
        "after_label": "修改后",
        "facts": [],
        "links": [],
        "tone": "positive",
    }


def _warning_item(item: dict) -> dict:
    excerpt = str(item.get("excerpt") or "").strip()
    return {
        "title": f"建议复听：{excerpt or '这一处'}",
        "text": str(item.get("message") or item.get("reason") or "现有信息无法确认，请结合原音听一下。"),
        "meta": "",
        "facts": [],
        "links": [],
        "tone": "warning",
    }


def _uncertain_warning(segment_id: str, excerpt: str, reason: str) -> dict:
    return {
        "code": "needs_confirmation",
        "segment_id": segment_id,
        "excerpt": excerpt,
        "message": reason or "这句话可能有识别错误，但现有资料还不能确认。",
    }


def _confidence(value: object) -> float:
    if isinstance(value, str):
        aliases = {"high": 0.9, "medium": 0.7, "low": 0.4}
        if value.casefold() in aliases:
            return aliases[value.casefold()]
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _ensure_active(is_cancelled: CancelCallback | None) -> None:
    if is_cancelled and is_cancelled():
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "字幕听写任务已取消")


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1_000))
