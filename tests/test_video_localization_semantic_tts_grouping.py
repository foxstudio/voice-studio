from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import draft_store, semantic_tts_grouping
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)


def _draft() -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(cue_id="cue_1", speaker_id="speaker_a", start_ms=0, end_ms=1000),
            VideoLocalizationCue(cue_id="cue_2", speaker_id="speaker_a", start_ms=1000, end_ms=2000),
            VideoLocalizationCue(cue_id="cue_3", speaker_id="speaker_b", start_ms=2000, end_ms=3000),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1000,
                text="先介绍事情的背景。",
                source_cue_ids=["cue_1"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=1000,
                end_ms=2000,
                text="接着说明具体做法。",
                source_cue_ids=["cue_2"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_3",
                start_ms=2000,
                end_ms=3000,
                text="另一个人作出回应。",
                source_cue_ids=["cue_3"],
            ),
        ],
    )


def test_build_items_uses_content_order_and_speaker_without_timestamps():
    items = semantic_tts_grouping.build_items(_draft())

    assert items == [
        {"subtitle_id": "localized_1", "text": "先介绍事情的背景。", "speaker_id": "speaker_a"},
        {"subtitle_id": "localized_2", "text": "接着说明具体做法。", "speaker_id": "speaker_a"},
        {"subtitle_id": "localized_3", "text": "另一个人作出回应。", "speaker_id": "speaker_b"},
    ]
    assert all("start_ms" not in item and "end_ms" not in item for item in items)


def test_validate_groups_accepts_complete_contiguous_same_speaker_groups():
    items = semantic_tts_grouping.build_items(_draft())

    groups = semantic_tts_grouping.validate_groups(
        [["localized_1", "localized_2"], ["localized_3"]],
        items,
        max_chars=80,
    )

    assert groups == [["localized_1", "localized_2"], ["localized_3"]]


@pytest.mark.parametrize(
    "groups,error",
    [
        ([["localized_1", "localized_3"], ["localized_2"]], "连续"),
        ([["localized_1", "localized_2", "localized_3"]], "说话人"),
        ([["localized_1"], ["localized_1", "localized_2"], ["localized_3"]], "遗漏、重复"),
    ],
)
def test_validate_groups_rejects_unsafe_results(groups, error):
    with pytest.raises(ValueError, match=error):
        semantic_tts_grouping.validate_groups(groups, semantic_tts_grouping.build_items(_draft()), 80)


def test_build_result_returns_stable_ui_groups():
    items = semantic_tts_grouping.build_items(_draft())
    result = semantic_tts_grouping.build_result(
        items,
        [["localized_1", "localized_2"], ["localized_3"]],
        target_chars=60,
        max_chars=80,
        llm_calls=[],
    )

    assert [group["subtitle_ids"] for group in result["groups"]] == [
        ["localized_1", "localized_2"],
        ["localized_3"],
    ]
    assert result["source_fingerprint"] == semantic_tts_grouping.source_fingerprint(
        semantic_tts_grouping.build_items(_draft())
    )
    assert result["llm_calls"] == []


def test_draft_save_preparation_drops_grouping_after_subtitle_content_changes():
    draft = _draft()
    grouping = {
        "source_fingerprint": semantic_tts_grouping.source_fingerprint(semantic_tts_grouping.build_items(draft)),
        "groups": [],
    }
    with_grouping = draft.model_copy(
        update={"localization_state": {"semantic_tts_grouping": grouping}}
    )
    assert "semantic_tts_grouping" in draft_store.with_fresh_gate(with_grouping, "now").localization_state

    changed_subtitles = list(with_grouping.localized_subtitles)
    changed_subtitles[0] = changed_subtitles[0].model_copy(update={"text": "字幕内容已经修改。"})
    changed = with_grouping.model_copy(update={"localized_subtitles": changed_subtitles})

    assert "semantic_tts_grouping" not in draft_store.with_fresh_gate(changed, "later").localization_state
