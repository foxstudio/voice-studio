from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
)
from app.domains.video_localization.tts_selection import (  # noqa: E402
    TtsSelectionRequest,
    build_selection_snapshot,
)
from tests.test_video_localization_localization_context_intent import (  # noqa: E402
    _draft,
)


def test_selecting_display_cards_uses_latest_user_text_and_timing():
    draft = _draft()
    source = draft.cues[0]
    word_ids = list(source.source_word_ids)
    spoken = VideoLocalizationSpokenSegment(
        segment_id="spoken_segment_0001",
        paragraph_id="paragraph_0001",
        text="这是完整的中文配音台词，只应该合成一次。",
        start_ms=int(source.start_ms or 0),
        end_ms=int(source.end_ms or 1),
        source_cue_ids=[source.cue_id],
        source_word_ids=word_ids,
    )
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=f"localized_cue_{index:04d}",
            start_ms=int(source.start_ms or 0) + (index - 1) * 100,
            end_ms=int(source.start_ms or 0) + index * 100,
            text=text,
            tts_text=f"用户修改后的{text}",
            source_cue_ids=[source.cue_id],
            source_word_ids=word_ids,
            spoken_segment_id=spoken.segment_id,
        )
        for index, text in enumerate(
            ["这是完整的中文配音台词，", "只应该合成一次。"],
            start=1,
        )
    ]
    draft = draft.model_copy(
        update={
            "localized_spoken_segments": [spoken],
            "localized_subtitles": subtitles,
        }
    )

    snapshot = build_selection_snapshot(
        draft,
        TtsSelectionRequest(
            target_subtitle_ids=[
                item.subtitle_id for item in subtitles
            ]
        ),
    )

    assert snapshot.target.text == (
        "用户修改后的这是完整的中文配音台词，\n"
        "用户修改后的只应该合成一次。"
    )
    assert snapshot.target.start_ms == subtitles[0].start_ms
    assert snapshot.target.end_ms == subtitles[-1].end_ms
    assert snapshot.source.cue_ids == [source.cue_id]
