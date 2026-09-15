from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class QualityRule:
    rule_id: str
    rule: str
    fatal: bool = False


_RULES: Mapping[str, QualityRule] = {
    "source_coverage": QualityRule(
        "source_coverage_complete",
        "每段源内容都必须能追溯到输出，不能整段遗漏、凭空增加或失去来源关系。",
        fatal=True,
    ),
    "timeline_integrity": QualityRule(
        "timeline_range_valid",
        "每个字幕片段都必须具有有效且连续的入点、出点和来源范围，不能出现倒置或损坏的时间结构。",
        fatal=True,
    ),
    "empty_or_punctuation": QualityRule(
        "localized_content_present",
        "承载源内容的本土化片段必须包含可理解、可朗读的文字，不能只有空白或标点。",
        fatal=True,
    ),
    "empty_segment": QualityRule(
        "asr_content_present",
        "有语音内容的识别片段必须包含可核对的源语言文字，不能只有空白或标点。",
        fatal=True,
    ),
    "number_integrity": QualityRule(
        "factual_literals_preserved",
        "数字、数量、版本号、单位和否定关系必须保持原意，不能在处理过程中被增加、遗漏或改写。",
        fatal=True,
    ),
    "literal_integrity": QualityRule(
        "asr_literals_preserved",
        "ASR 校对不能擅自改变原始识别中的数字、单位、版本号或否定关系。",
        fatal=True,
    ),
    "language_integrity": QualityRule(
        "asr_source_language_preserved",
        "ASR 校对必须保留源语言，不能把识别文本改写成目标语言或另一种语言。",
        fatal=True,
    ),
    "proper_nouns": QualityRule(
        "proper_name_consistency",
        "名称只在上下文重复证据或可靠资料足够时修正，并在全文保持一致。",
    ),
    "contextual_asr_error": QualityRule(
        "asr_high_confidence_correction",
        "ASR 只修正有上下文或检索证据支持的高可信错字、近音词和短语识别错误。",
    ),
    "contextual_asr_uncertainty": QualityRule(
        "asr_context_needs_confirmation",
        "每句 ASR 都要结合全文背景判断；有疑点但证据不足时保留原文，并用大白话提醒用户确认。",
    ),
    "punctuation_boundaries": QualityRule(
        "asr_boundary_readability",
        "ASR 断句应依据语音停顿、语义完整性和上屏可读性，不改变原话内容。",
    ),
    "translationese": QualityRule(
        "natural_chinese_expression",
        "中文表达应符合当前人物和场景中的母语习惯，不能照搬外语语序或生造搭配。",
    ),
    "collocation": QualityRule(
        "natural_chinese_collocation",
        "中文主谓、动宾和修饰关系应自然成立，不能出现母语者通常不会使用的搭配。",
    ),
    "fluency": QualityRule(
        "natural_spoken_fluency",
        "配音稿应能被当前人物自然说出口，不应有明显残句、倒装或生硬书面表达。",
    ),
    "naturalness": QualityRule(
        "natural_spoken_fluency",
        "配音稿应能被当前人物自然说出口，不应有明显残句、倒装或生硬书面表达。",
    ),
    "coherence": QualityRule(
        "document_context_coherence",
        "相邻表达应在人物、指代、动作和逻辑上连贯，不能因局部分段造成意思错位。",
    ),
    "semantic_ownership": QualityRule(
        "semantic_ownership_preserved",
        "每项意思必须属于正确的人物、动作和语义位置，不能在相邻片段间张冠李戴。",
    ),
    "fidelity": QualityRule(
        "meaning_fidelity",
        "本土化应完整保留原文事实、主体、对象、因果和信息顺序，同时允许自然重组中文语序。",
    ),
    "persona": QualityRule(
        "speaker_persona_consistency",
        "人物身份、语气、情绪和说话关系应前后一致，不能把不同人物抹成同一种口吻。",
    ),
    "timing_segmentation": QualityRule(
        "semantic_segmentation_readable",
        "字幕分段应兼顾语义完整和上屏可读性，不能造成重复、漏译、错序或理解中断。",
    ),
    "adjacent_duplicate": QualityRule(
        "no_accidental_duplicate",
        "相邻字幕不能因映射或返修重复承载同一段内容。",
    ),
    "display_budget": QualityRule(
        "subtitle_reading_budget",
        "字幕应在对应时长内保持可读、可朗读，超限内容需要重分段或保守精简。",
    ),
    "editorial_quality": QualityRule(
        "localized_editorial_quality",
        "最终中文稿应同时满足准确、连贯、自然和人物口吻一致，主观偏好不应阻止任务完成。",
    ),
    "review_protocol": QualityRule(
        "llm_review_contract",
        "质量复核结果应符合约定结构；复核器暂时不可用或格式异常时保留已有结果并提示后续复核。",
    ),
}

_DEFAULT_RULE = QualityRule(
    "quality_review_follow_up",
    "发现的问题应先进入问题池定向修复并重新复核；无法确定的低影响问题保留给用户人工检查。",
)


def annotate_issue(issue: dict, *, category: str | None = None) -> dict:
    normalized_category = str(category or issue.get("category") or "editorial_quality").strip().casefold()
    rule = _RULES.get(normalized_category, _DEFAULT_RULE)
    severity = str(issue.get("severity") or "minor").strip().casefold()
    fatal = rule.fatal
    if fatal:
        action = "stop"
    elif severity in {"major", "blocker"}:
        action = "repair_then_warn"
    else:
        action = "warn"
    return {
        **issue,
        "category": normalized_category,
        "rule_id": rule.rule_id,
        "rule": rule.rule,
        "fatal": fatal,
        "action": action,
    }


def fatal_issues(issues: list[dict]) -> list[dict]:
    return [issue for issue in issues if bool(issue.get("fatal"))]
