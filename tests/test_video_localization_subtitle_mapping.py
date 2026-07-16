from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import subtitles, tts_pipeline  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft  # noqa: E402
from app.errors import AppException  # noqa: E402


def _cue(cue_id: str, start_ms: int, end_ms: int, *, speaker_id: str = "speaker_a") -> VideoLocalizationCue:
    return VideoLocalizationCue(
        cue_id=cue_id,
        speaker_id=speaker_id,
        start_ms=start_ms,
        end_ms=end_ms,
        en_subtitle_text=f"Source {cue_id}",
        tts_recommended_text=f"Existing TTS {cue_id}",
    )


def test_zh_srt_import_maps_multiple_subtitles_to_one_source_cue():
    draft = VideoLocalizationDraft(cues=[_cue("cue_0001", 0, 2000)])

    imported = subtitles.import_srt(
        draft,
        "zh",
        """1
00:00:00,100 --> 00:00:00,900
第一条

2
00:00:01,100 --> 00:00:01,900
第二条
""",
        overwrite_tts=False,
    )

    assert [item.source_cue_ids for item in imported.localized_subtitles] == [
        ["cue_0001"],
        ["cue_0001"],
    ]
    assert imported.cues[0].zh_localized_subtitle_text == "第一条\n第二条"
    assert imported.cues[0].tts_recommended_text == "Existing TTS cue_0001"


def test_zh_srt_import_maps_one_subtitle_to_all_adjacent_overlapping_source_cues():
    draft = VideoLocalizationDraft(
        cues=[
            _cue("cue_0001", 0, 1000),
            _cue("cue_0002", 1000, 2000),
            _cue("cue_0003", 2000, 3000),
        ]
    )

    imported = subtitles.import_srt(
        draft,
        "zh",
        "1\n00:00:00,200 --> 00:00:01,800\n合并字幕\n",
        overwrite_tts=True,
    )

    subtitle = imported.localized_subtitles[0]
    assert subtitle.linked_cue_id == "cue_0001"
    assert subtitle.source_cue_ids == ["cue_0001", "cue_0002"]
    assert [cue.zh_localized_subtitle_text for cue in imported.cues] == ["合并字幕", "合并字幕", None]
    assert [cue.tts_recommended_text for cue in imported.cues] == ["合并字幕", "合并字幕", None]


def test_zh_srt_import_preserves_cross_speaker_source_ids_for_tts_rejection(tmp_path: Path):
    draft = VideoLocalizationDraft(
        cues=[
            _cue("cue_0001", 0, 1000, speaker_id="speaker_a"),
            _cue("cue_0002", 1000, 2000, speaker_id="speaker_b"),
        ]
    )
    imported = subtitles.import_srt(
        draft,
        "zh",
        "1\n00:00:00,100 --> 00:00:01,900\n跨人物字幕\n",
        overwrite_tts=True,
    )

    assert imported.localized_subtitles[0].source_cue_ids == ["cue_0001", "cue_0002"]
    with pytest.raises(AppException) as exc_info:
        tts_pipeline.build_batch_request(
            project_id="project_001",
            project_name="跨人物导入",
            draft=imported,
            output_dir=tmp_path / "output",
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TTS_CROSS_SPEAKER_SUBTITLE"
    assert exc_info.value.detail_dict["source_cue_ids"] == ["cue_0001", "cue_0002"]
