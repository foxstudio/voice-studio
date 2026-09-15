"""Serializable, privacy-safe LLM call records for video-localization tasks."""

from __future__ import annotations

from threading import Lock

from app.domains.video_localization.llm_contracts import (
    AsrLlmCallPurpose,
    AsrLlmCallRecord,
    VideoLocalizationLlmCallPurpose,
)
from app.services.llm_runtime import LlmCompletionTrace, TraceSink


class AsrLlmTraceCollector:
    """Thread-safe collection of privacy-safe LLM completion traces."""

    def __init__(self) -> None:
        self._records: list[AsrLlmCallRecord] = []
        self._lock = Lock()

    def sink(
        self,
        *,
        call_id: str,
        purpose: AsrLlmCallPurpose,
        round_index: int,
        candidate_ids: list[str] | None = None,
        question_id: str | None = None,
    ) -> TraceSink:
        def capture(trace: LlmCompletionTrace) -> None:
            record = AsrLlmCallRecord.from_runtime(
                trace,
                call_id=call_id,
                purpose=purpose,
                round_index=round_index,
                candidate_ids=candidate_ids,
                question_id=question_id,
            )
            with self._lock:
                self._records.append(record)

        return capture

    def records(self) -> list[AsrLlmCallRecord]:
        with self._lock:
            return sorted(self._records, key=lambda item: item.call_id)


_PURPOSE_LABELS = {
    "document_understanding": "理解全文",
    "query_rewrite": "改写搜索词",
    "evidence_assessment": "判断资料是否足够",
    "visual_analysis": "识别画面",
    "entity_resolution": "确认规范名称",
    "entity_variant_mapping": "定位全文名称变体",
    "section_review": "检查听写区块",
    "review_decisions": "判断修改建议",
    "whole_recheck": "重新通读全文",
    "localization_document_brief": "建立全文本土化创作提纲",
    "localization_document_evidence_adjudication": "确认资料与画面结论",
    "localization_spoken_script_generation": "生成全文本土化初稿",
    "localization_fidelity_review": "复核原意与事实",
    "localization_naturalness_review": "盲测中文自然度",
    "localization_spoken_script_finalization": "本土化台词终审",
    "localization_fidelity_closure_review": "验收定点修订",
    "localization_naturalness_closure_review": "验收修改句自然度",
    "localization_fidelity_finalization_regression": "复核修改后原意",
    "localization_naturalness_finalization_regression": "复核修改后自然度",
    "localization_alignment_adjudication": "裁决语义时间歧义",
    "semantic_tts_grouping": "判断配音语义分组",
}

_PROVIDER_LABELS = {
    "local-codex-cli": "本地 Codex CLI",
    "openrouter.ai": "OpenRouter API",
    "api.openai.com": "OpenAI API",
}


# Compatibility aliases let newer localization tasks use domain-wide names
# without changing the serialized ASR contracts that already expose these
# record classes.
VideoLocalizationLlmCallRecord = AsrLlmCallRecord
VideoLocalizationLlmTraceCollector = AsrLlmTraceCollector


def _provider_label(provider_host: str | None) -> str:
    value = str(provider_host or "").strip()
    if not value:
        return "服务未返回"
    if value in _PROVIDER_LABELS:
        return f"{_PROVIDER_LABELS[value]}（{value}）"
    if value in {"127.0.0.1", "localhost"}:
        return f"本地 OpenAI 兼容接口（{value}）"
    return value


def _distinct_values(
    llm_calls: list[AsrLlmCallRecord],
    attribute: str,
    *,
    transform=None,
) -> list[str]:
    values: list[str] = []
    for item in llm_calls:
        raw_value = getattr(item, attribute, None)
        value = (
            transform(raw_value)
            if transform is not None
            else str(raw_value or "").strip()
        )
        if value and value not in values:
            values.append(value)
    return values


def _joined_identity(values: list[str]) -> str:
    return "、".join(values) if values else "服务未返回"


def _aggregate_usage_label(llm_calls, attribute: str, *, calls_complete: bool) -> str:
    values = [getattr(item, attribute, None) for item in llm_calls]
    known = [value for value in values if value is not None]
    formatted = (
        f"${sum(known):.6f}"
        if attribute == "cost_usd"
        else f"{sum(known):,}"
    )
    if calls_complete and len(known) == len(values):
        return formatted
    return f"未知（已记录 {formatted}）" if known else "未知"


def project_llm_calls(
    llm_calls: list[AsrLlmCallRecord],
    *,
    calls_complete: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Project privacy-safe call records into the shared task-detail shape.

    ``calls_complete`` is only true when the atomic result explicitly records
    every call. It distinguishes a verified zero-call result from legacy data
    that may simply lack observability records.
    """

    items = []
    for item in llm_calls:
        purpose = str(getattr(item, "purpose", "") or "")
        finish_reason = getattr(item, "finish_reason", None)
        error_code = getattr(item, "error_code", None)
        items.append(
            {
                "title": _PURPOSE_LABELS.get(
                    purpose,
                    "模型调用",
                ),
                "text": (
                    "正常结束"
                    if finish_reason == "stop" and not error_code
                    else (
                        f"调用异常：{error_code}"
                        if error_code
                        else (
                            "停止原因："
                            f"{finish_reason or '服务未返回'}"
                        )
                    )
                ),
                "meta": (
                    f"第 {getattr(item, 'round_index', 1)} 轮 · "
                    f"{getattr(item, 'duration_ms', 0) / 1000:.1f} 秒"
                ),
                "tone": "warning" if error_code else "positive",
                "facts": [
                    {
                        "label": "输入规模",
                        "value": (
                            f"{getattr(item, 'request_chars', 0):,} 字符"
                        ),
                    },
                    *(
                        [
                            {
                                "label": "输入 Token",
                                "value": _integer_or_unavailable(
                                    getattr(item, "prompt_tokens", None)
                                ),
                            },
                            {
                                "label": "输出 Token",
                                "value": _integer_or_unavailable(
                                    getattr(
                                        item,
                                        "completion_tokens",
                                        None,
                                    )
                                ),
                            },
                            {
                                "label": "其中思考",
                                "value": _reasoning_tokens_label(
                                    getattr(
                                        item,
                                        "reasoning_tokens",
                                        None,
                                    )
                                ),
                            },
                            {
                                "label": "费用",
                                "value": _cost_or_unavailable(
                                    getattr(item, "cost_usd", None)
                                ),
                            },
                        ]
                        if len(llm_calls) > 1
                        else []
                    ),
                    {
                        "label": "推理强度",
                        "value": (
                            getattr(
                                item,
                                "reasoning_effort_requested",
                                None,
                            )
                            or "服务默认"
                        ),
                    },
                ],
                "links": [],
            }
        )

    metrics = []
    if llm_calls:
        metrics.extend(
            [
                {
                    "label": "调用模型",
                    "value": _joined_identity(
                        _distinct_values(llm_calls, "model_id")
                    ),
                },
                {
                    "label": "调用方式",
                    "value": _joined_identity(
                        _distinct_values(
                            llm_calls,
                            "provider_host",
                            transform=_provider_label,
                        )
                    ),
                },
                {
                    "label": "模型配置",
                    "value": _joined_identity(
                        _distinct_values(llm_calls, "profile_id")
                    ),
                },
            ]
        )
    metrics.extend(
        [
            {
                "label": "模型调用",
                "value": (
                    str(len(llm_calls))
                    if calls_complete
                    else f"未知（已记录 {len(llm_calls)}）" if llm_calls else "未知"
                ),
            },
            *[
                {
                    "label": label,
                    "value": _aggregate_usage_label(
                        llm_calls, attribute, calls_complete=calls_complete,
                    ),
                }
                for label, attribute in (
                    ("输入 Token", "prompt_tokens"),
                    ("输出 Token", "completion_tokens"),
                    ("其中思考", "reasoning_tokens"),
                    ("模型费用", "cost_usd"),
                )
            ],
        ]
    )
    return items, metrics


def enrich_task_step_with_llm_calls(
    step_result: dict,
    atomic_result: object,
) -> dict:
    """Attach one stable debug projection to formal and development results."""

    llm_calls = list(getattr(atomic_result, "llm_calls", []) or [])
    call_items, call_metrics = project_llm_calls(
        llm_calls,
        calls_complete=getattr(
            getattr(atomic_result, "quality_summary", None),
            "call_telemetry_complete",
            None,
        ) is not False,
    )
    atomic_input = getattr(atomic_result, "input", None)
    metrics = []
    for label, value in (
        (
            "输入契约",
            getattr(atomic_input, "contract_version", None),
        ),
        (
            "上游契约",
            getattr(atomic_input, "upstream_contract_version", None),
        ),
        (
            "输出契约",
            getattr(atomic_result, "contract_version", None),
        ),
        (
            "上游任务",
            getattr(atomic_input, "upstream_operation_id", None),
        ),
        (
            "来源音轨",
            getattr(atomic_input, "source_track_id", None),
        ),
        (
            "模型配置",
            getattr(atomic_result, "profile_id", None),
        ),
        (
            "实际模型",
            getattr(atomic_result, "model_id", None),
        ),
        (
            "停止原因",
            getattr(atomic_result, "stop_reason", None),
        ),
    ):
        if llm_calls and label in {"模型配置", "实际模型"}:
            continue
        normalized = str(value or "").strip()
        if normalized:
            metrics.append({"label": label, "value": normalized})
    existing_debug = step_result.get("debug")
    if not isinstance(existing_debug, dict):
        existing_debug = {}

    existing_metrics = list(existing_debug.get("metrics") or [])
    for metric in [*metrics, *call_metrics]:
        if metric not in existing_metrics:
            existing_metrics.append(metric)

    existing_sections = list(existing_debug.get("sections") or [])
    legacy_items = list(existing_debug.get("items") or [])
    if legacy_items:
        legacy_section = {
            "title": "节点诊断",
            "items": legacy_items,
        }
        if legacy_section not in existing_sections:
            existing_sections.append(legacy_section)
    if call_items and not any(
        section.get("title") == "模型调用明细"
        for section in existing_sections
        if isinstance(section, dict)
    ):
        existing_sections.append(
            {"title": "模型调用明细", "items": call_items}
        )

    existing_notes = [
        str(item)
        for item in (existing_debug.get("notes") or [])
        if str(item).strip()
    ]
    if not existing_debug:
        existing_notes.append(
            "这里只保存用量统计，不保存完整提问、回答正文或隐藏思考。"
        )
    for warning in getattr(atomic_result, "warnings", []) or []:
        normalized_warning = str(warning).strip()
        if normalized_warning and normalized_warning not in existing_notes:
            existing_notes.append(normalized_warning)

    return {
        **step_result,
        "debug": {
            "description": (
                str(existing_debug.get("description") or "").strip()
                or "用于核对输入来源、模型调用、Token、费用和结束原因。"
            ),
            "metrics": existing_metrics,
            "sections": existing_sections,
            "notes": existing_notes,
        },
    }


def _integer_or_unavailable(value: int | None) -> str:
    return f"{value:,}" if value is not None else "服务未返回"


def _reasoning_tokens_label(value: int | None) -> str:
    return f"{value:,} Token" if value is not None else "服务未返回"


def _cost_or_unavailable(value: float | None) -> str:
    return f"${value:.6f}" if value is not None else "服务未返回"


__all__ = [
    "AsrLlmCallPurpose",
    "AsrLlmCallRecord",
    "AsrLlmTraceCollector",
    "VideoLocalizationLlmCallPurpose",
    "VideoLocalizationLlmCallRecord",
    "VideoLocalizationLlmTraceCollector",
    "enrich_task_step_with_llm_calls",
    "project_llm_calls",
]
