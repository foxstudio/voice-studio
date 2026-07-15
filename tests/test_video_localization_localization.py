from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationDraft  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.models.schemas import LlmProviderListResponse, LlmProviderProfile  # noqa: E402


def _configure_llm(monkeypatch):
    profile = LlmProviderProfile(
        profile_id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-chat",
        enabled=True,
        api_key_configured=True,
    )
    monkeypatch.setattr(
        localization.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id=profile.profile_id),
    )
    monkeypatch.setattr(localization.settings_store, "llm_profile", lambda profile_id: profile)
    return profile


def _draft(source: str = "In 1992, this changed everything.") -> VideoLocalizationDraft:
    return VideoLocalizationDraft.model_validate(
        {
            "speakers": [{"speaker_id": "speaker_01", "display_name": "Alex", "notes": "克制、直接"}],
            "scene_context": "一位视觉特效创作者在讲解制作流程",
            "glossary": [
                {
                    "source_text": "Seedance",
                    "corrected_source_text": "Seedance",
                    "zh_text": "即梦 Seedance",
                    "notes": "产品名称",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "en_subtitle_text": source,
                }
            ],
        }
    )


def test_llm_localization_uses_safe_l1_w0_and_keeps_audit_note(monkeypatch):
    _configure_llm(monkeypatch)
    captured = {}

    def complete_json(**kwargs):
        captured.update(kwargs)
        return {
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "zh_subtitle_text": "1992 年，这件事改变了一切",
                    "adaptation_note": "按中文口语重组语序",
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    cue = localization.with_chinese_draft(_draft()).cues[0]

    assert cue.zh_localized_subtitle_text == "1992 年，这件事改变了一切"
    assert cue.tts_recommended_text is None
    assert "llm_localized" in cue.quality_flags
    assert "localization:L1_W0" in cue.quality_flags
    assert "本土化说明：按中文口语重组语序" == cue.notes
    assert captured["user_payload"]["profile"]["worldview_permeability"] == "W0"
    assert captured["user_payload"]["profile"]["scene_context"] == "一位视觉特效创作者在讲解制作流程"
    assert captured["user_payload"]["glossary"][0]["zh_text"] == "即梦 Seedance"


def test_llm_localization_rejects_changed_or_missing_number(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "zh_subtitle_text": "1993 年，这件事改变了一切",
                    "adaptation_note": "",
                }
            ]
        },
    )

    cue = localization.with_chinese_draft(_draft()).cues[0]

    assert cue.zh_localized_subtitle_text.startswith("【待本土化】")
    assert "llm_localization_failed" in cue.quality_flags
    assert "llm_localized" not in cue.quality_flags


def test_existing_localized_subtitle_is_not_sent_to_llm_or_tts_normalizer(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("已有中文不应再提交给 LLM")),
    )
    draft = _draft("In 1992, 130 people joined.")
    draft = draft.model_copy(
        update={
            "cues": [draft.cues[0].model_copy(update={"zh_localized_subtitle_text": "1992 年，有 130 人加入。"})]
        }
    )

    try:
        localization.with_chinese_draft(draft)
    except AppException as exc:
        assert exc.code == "VIDEO_LOCALIZATION_LOCALIZATION_UNCHANGED"
    else:
        raise AssertionError("已有中文轨不应被本土化或 TTS 阶段重复修改")
