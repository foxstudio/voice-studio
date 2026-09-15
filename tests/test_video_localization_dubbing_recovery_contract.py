from types import SimpleNamespace as NS

import pytest
from app.errors import AppException
from app.domains.video_localization.dubbing_recovery import validate_recovery_decision
from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision


def decision(**updates):
    return DubbingRecoveryDecision(
        **dict(
            recovery_id="a12345678901",
            source_revision="a" * 64,
            plan_revision=1,
            group_id="group",
            stage="semantic_phrases",
            phrases=["第一句。", "第二句。"],
            reason="两个完整语义句",
            **updates,
        )
    )


def draft():
    group = NS(group_id="group", spoken_text="第一句。第二句。", speaker_id="speaker")
    return NS(
        dubbing_production=NS(active_plan=NS(groups=[group], source_revision="a" * 64, plan_revision=1)),
        cues=[NS(cue_id="cue", speaker_id="speaker", start_ms=1000, end_ms=5000)],
    )


def test_recovery_freezes_exact_order_and_current_plan():
    d = draft()
    assert validate_recovery_decision(d, decision())[1] is None
    d.dubbing_production.active_plan.plan_revision = 2
    with pytest.raises(AppException, match="当前配音计划"):
        validate_recovery_decision(d, decision())


@pytest.mark.parametrize("phrases", [["第一句。", "第一句。"], ["第二句。", "第一句。"], ["第一句。", "漏字。"]])
def test_recovery_rejects_missing_duplicate_or_reordered_speech(phrases):
    request = decision().model_copy(update={"phrases": phrases})
    with pytest.raises(AppException, match="完整覆盖"):
        validate_recovery_decision(draft(), request)


def test_reference_recovery_requires_same_speaker_contiguous_cues():
    d = draft()
    request = decision().model_copy(update={"stage": "nearby_reference", "reference_cue_ids": ["cue"]})
    assert validate_recovery_decision(d, request)[1] == (1000, 5000)
    d.cues[0].speaker_id = "other"
    with pytest.raises(AppException, match="当前说话人"):
        validate_recovery_decision(d, request)
    d.cues[0].speaker_id = "speaker"
    d.cues.append(NS(cue_id="overlap", speaker_id="speaker", start_ms=3000, end_ms=4000))
    with pytest.raises(AppException, match="未选择"):
        validate_recovery_decision(d, request)
