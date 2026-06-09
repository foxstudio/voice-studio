from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.schemas import GeneratePlanResponse, PlannedTextSegment


@dataclass(frozen=True)
class EnginePlanPolicy:
    threshold: int
    hard_threshold: int
    target_chars: int
    max_chars: int
    recommended_action: str
    warning: str


_POLICIES = {
    "omnivoice": EnginePlanPolicy(
        threshold=80,
        hard_threshold=150,
        target_chars=50,
        max_chars=65,
        recommended_action="split_generate",
        warning="OmniVoice 单句不宜过长，建议分段生成以获得更稳定的输出。",
    ),
    "indextts-v2": EnginePlanPolicy(
        threshold=300,
        hard_threshold=600,
        target_chars=180,
        max_chars=220,
        recommended_action="split_verify_merge",
        warning="IndexTTS v2 支持长文本内部切段，但产品层建议分段生成、校对后再合并。",
    ),
    "mimo-v2.5-tts-preset": EnginePlanPolicy(
        threshold=600,
        hard_threshold=1200,
        target_chars=320,
        max_chars=400,
        recommended_action="split_verify_merge",
        warning="MiMo 云端可处理较长文本，但分段生成更便于校对和重试。",
    ),
    "mimo-v2.5-tts-voiceclone": EnginePlanPolicy(
        threshold=400,
        hard_threshold=800,
        target_chars=200,
        max_chars=250,
        recommended_action="split_verify_merge",
        warning="MiMo 音色复刻长文本建议分段生成，降低重试成本并减少漏句风险。",
    ),
    "mimo-v2.5-tts-voicedesign": EnginePlanPolicy(
        threshold=400,
        hard_threshold=800,
        target_chars=200,
        max_chars=250,
        recommended_action="split_generate",
        warning="MiMo 文本音色设计建议先用短样本确认声线，长文本再分段生成。",
    ),
}

_DEFAULT_POLICY = EnginePlanPolicy(
    threshold=300,
    hard_threshold=600,
    target_chars=180,
    max_chars=220,
    recommended_action="split_verify_merge",
    warning="当前文本较长，建议分段生成并校对。",
)


def plan_text(*, text: str, engine_id: str, planner_mode: str = "auto", target_format: str = "mp3") -> GeneratePlanResponse:
    del target_format
    normalized = text.strip()
    policy = _POLICIES.get(engine_id, _DEFAULT_POLICY)
    text_length = _char_count(normalized)
    if text_length <= policy.threshold:
        return GeneratePlanResponse(
            planner="rules",
            llm_available=False,
            mode="direct",
            recommended_action="direct_generate_with_verification",
            requires_user_confirmation=False,
            text_length=text_length,
            threshold=policy.threshold,
            hard_threshold=policy.hard_threshold,
            privacy_notice=_privacy_notice(planner_mode),
            planner_reason="文本长度处于当前引擎的直接生成建议范围内。",
            segments=[
                PlannedTextSegment(
                    index=1,
                    text=normalized,
                    char_count=text_length,
                    segment_reason="direct_text",
                )
            ]
            if normalized
            else [],
        )

    mode = "longform_strongly_recommended" if text_length > policy.hard_threshold else "longform_recommended"
    segments = _build_segments(normalized, policy)
    warnings = [policy.warning]
    if mode == "longform_strongly_recommended":
        warnings.append("文本已超过当前引擎强提醒阈值，直接单条生成更容易出现等待过久、漏句或截断。")
    if planner_mode == "llm":
        warnings.append("LLM 规划尚未启用，当前使用规则规划。")

    return GeneratePlanResponse(
        planner="rules",
        llm_available=False,
        mode=mode,
        recommended_action=policy.recommended_action,  # type: ignore[arg-type]
        requires_user_confirmation=True,
        text_length=text_length,
        threshold=policy.threshold,
        hard_threshold=policy.hard_threshold,
        warnings=warnings,
        privacy_notice=_privacy_notice(planner_mode),
        planner_reason=f"文本长度 {text_length} 已超过 {engine_id} 的建议阈值 {policy.threshold}。",
        segments=segments,
    )


def _privacy_notice(planner_mode: str) -> str:
    if planner_mode == "llm":
        return "LLM 规划尚未启用；当前规则规划不会离开本机。启用云端 ASR、MiMo voiceclone 或未来 LLM 时会另行提示。"
    return "规则规划不会离开本机。启用云端 ASR、MiMo voiceclone 或未来 LLM 时会另行提示。"


def _char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _build_segments(text: str, policy: EnginePlanPolicy) -> list[PlannedTextSegment]:
    units = _split_units(text, policy.max_chars)
    merged = _merge_units(units, policy)
    return [
        PlannedTextSegment(
            index=index + 1,
            text=item,
            char_count=_char_count(item),
            segment_reason="sentence_boundary" if _char_count(item) <= policy.max_chars else "length_fallback",
        )
        for index, item in enumerate(merged)
        if item.strip()
    ]


def _split_units(text: str, max_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text.strip()) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs or [text.strip()]:
        sentence_units = [part.strip() for part in re.split(r"(?<=[。！？!?；;…])\s*|\n+", paragraph) if part.strip()]
        for unit in sentence_units or [paragraph]:
            units.extend(_split_long_unit(unit, max_chars))
    return units


def _split_long_unit(text: str, max_chars: int) -> list[str]:
    if _char_count(text) <= max_chars:
        return [text]
    weak_parts = [part.strip() for part in re.split(r"(?<=[，,、：:])\s*", text) if part.strip()]
    if len(weak_parts) > 1:
        split: list[str] = []
        for part in weak_parts:
            split.extend(_split_long_unit(part, max_chars))
        return split
    return _split_by_length(text, max_chars)


def _split_by_length(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if _char_count(current) >= max_chars:
            pieces.append(current.strip())
            current = ""
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _merge_units(units: list[str], policy: EnginePlanPolicy) -> list[str]:
    merged: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{unit}" if current else unit
        if current and _char_count(candidate) > policy.max_chars:
            merged.append(current.strip())
            current = unit
        else:
            current = candidate
        if _char_count(current) >= policy.target_chars:
            merged.append(current.strip())
            current = ""
    if current.strip():
        if merged and _char_count(current) < max(30, policy.target_chars // 3):
            candidate = f"{merged[-1]}{current}"
            if _char_count(candidate) <= policy.max_chars:
                merged[-1] = candidate
            else:
                merged.append(current.strip())
        else:
            merged.append(current.strip())
    return merged
