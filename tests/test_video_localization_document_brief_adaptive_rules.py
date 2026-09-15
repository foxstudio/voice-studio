"""Check shared rule selection used by the production staged brief executor."""
from __future__ import annotations

from itertools import product
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization_document_brief as brief  # noqa: E402
from app.domains.video_localization.localization_context_intent import LocalizationSourceUncertainty  # noqa: E402
from app.domains.video_localization.localization_source import LocalizationSourceGlossaryEntry, LocalizationSourceSpeaker  # noqa: E402
from tests.test_video_localization_localization_document_brief import _inputs  # noqa: E402


# Frozen from the original full-prompt builder, not derived from the new helper.
RULES = [
    ("respect_locked_glossary", "输入包含已审核术语表：术语关系必须优先遵守 glossary，只有存在冲突才列为证据问题。"),
    ("infer_textual_persona_without_voice_claims", "没有可靠说话人档案：只根据文本推断口吻，不得猜年龄、地域、性别或声音特征。"),
    ("reuse_asr_speaker_style", "上游已保存说话方式摘要：用它校准人设，但仍以全文实际表达为准。"),
    ("resolve_upstream_source_uncertainties", "上游 ASR 全文理解已附带少量罕见、待核实词及准确 cue。"
     "这些词不是已确认人名或事实，不能直接按音译解释。必须结合完整上下文"
     "决定真实含义；若画面可能有硬字幕、界面文字或可读台词，交给 visual "
     "evidence question，若画面无字则明确保留不确定性。"),
    ("preserve_speakable_persona", "本次包含配音台词：简报必须记录可朗读节奏、情绪和有"
     "人设价值的口语不流畅；这里只生成台词文本，不代表生成音频。"),
    ("prioritize_unresolved_asr_evidence", "source_cues 中标记 asr_unresolved_text 的文字已经被 ASR 复查确认仍不可靠。"
     "不要把它当成正常原文直接解释：先结合内容类型判断最合适的补证方式；"
     "影视、剧情、游戏或表演内容如果画面可能有硬字幕、界面文字或角色台词"
     "文字，必须为对应 cue 建立 visual evidence question。没有可读画面文字时，"
     "后续创作应保留不确定性，不得猜写具体含义。"),
    ("pragmatic_cultural_equivalence", "文化迁移必须按语用功能和强度建模，不能建立固定中英词表；"
     "需要最新用语或文化背景时列一个窄查询，不要为了普通口语搜索。"),
]


def _request(*, glossary, speakers, style, uncertainties, spoken, unresolved, strategy):
    source, context, route = _inputs()
    source = source.model_copy(update={"input": source.input.model_copy(update={
        "glossary": [LocalizationSourceGlossaryEntry(glossary_id="g1", source_text="Example")] if glossary else [],
        "speakers": [LocalizationSourceSpeaker(speaker_id="speaker_1")] if speakers else [],
        "cues": [cue.model_copy(update={"quality_flags": ["asr_unresolved_text"] if unresolved else ["timing:high"]})
                 for cue in source.input.cues],
    })})
    document = context.input.document_context.model_copy(update={
        "speaker_style": "审慎、直接" if style else "",
        "source_uncertainties": [LocalizationSourceUncertainty(
            term="unverified", reason="上游尚未确认", source_cue_ids=[source.input.cues[0].cue_id],
        )] if uncertainties else [],
    })
    delivery = context.input.delivery_intent.model_copy(update={
        "deliverables": context.input.delivery_intent.deliverables.model_copy(update={"spoken_script": spoken}),
    })
    context = context.model_copy(update={"input": context.input.model_copy(update={
        "document_context": document, "delivery_intent": delivery,
    })})
    return brief.LocalizationDocumentBriefInput(
        source_operation_id="source", context_operation_id="context", source_lock=source,
        context_intent=context, route=route.model_copy(update={"prompt_strategy": strategy}),
    )


@pytest.mark.parametrize("strategy", ["adaptive", "fixed"])
@pytest.mark.parametrize("switches", list(product([False, True], repeat=6)))
def test_all_rule_activation_combinations_preserve_rule_selection(strategy, switches, monkeypatch):
    glossary, speakers, style, uncertainties, spoken, unresolved = switches
    request = _request(glossary=glossary, speakers=speakers, style=style, uncertainties=uncertainties,
                       spoken=spoken, unresolved=unresolved, strategy=strategy)
    before = request.model_dump(mode="json")
    monkeypatch.setattr(brief.llm_runtime, "complete_json", lambda *a, **k: pytest.fail("Pure rule selection must not call models"))
    expected = [rule for enabled, rule in zip(
        [glossary, not speakers, style, uncertainties, spoken, unresolved, True], RULES,
    ) if enabled] if strategy == "adaptive" else []
    adaptive = brief.build_document_brief_adaptive_rules(request)
    assert adaptive.rule_ids == [rule_id for rule_id, _ in expected]
    assert adaptive.paragraphs == [paragraph for _, paragraph in expected]
    assert brief.LocalizationDocumentBriefAdaptiveRules.model_validate(adaptive.model_dump(mode="json")) == adaptive
    assert request.model_dump(mode="json") == before
    assert brief.PROMPT_VERSION == "localization-document-brief-prompt-v14"
