from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_dual_tracks,
    localization_tracks,
)
from tests.test_video_localization_localization_dual_tracks import (  # noqa: E402
    _fixture,
)
from tests.test_video_localization_localization_source import (  # noqa: E402
    _draft,
)


def test_tracks_quality_gate_blocks_stale_source_and_accepts_same_version():
    script, adjudicated = _fixture()
    adjudicated = adjudicated.model_copy(
        update={
            "blocks": [
                block.model_copy(
                    update={
                        "source_start_ms": index * 4_000,
                        "source_end_ms": (index + 1) * 4_000,
                    }
                )
                for index, block in enumerate(adjudicated.blocks)
            ]
        }
    )
    dual = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )
    request = localization_tracks.LocalizationTracksQualityGateInput(
        dual_tracks_operation_id="dual_tracks_operation",
        source_fingerprint=script.source_fingerprint,
        final_script=script,
        dual_tracks=dual,
    )

    accepted = localization_tracks.validate_localization_tracks(
        request,
        current_source_fingerprint=script.source_fingerprint,
    )
    stale = localization_tracks.validate_localization_tracks(
        request,
        current_source_fingerprint="f" * 64,
    )

    assert accepted.decision in {"passed", "warning"}
    assert not accepted.blockers
    assert stale.decision == "blocked"
    assert stale.blockers[0].code == "source_changed"


def test_formal_localized_subtitles_use_shared_display_exit_timing():
    display_cues = [
        SimpleNamespace(
            cue_id="localized-1",
            start_ms=1_000,
            end_ms=1_100,
            text="第一条",
            tts_text="第一条",
            source_cue_ids=["cue-1"],
            source_word_ids=["word-1"],
            paragraph_id="paragraph-1",
            quality_flags=[],
        ),
        SimpleNamespace(
            cue_id="localized-2",
            start_ms=2_000,
            end_ms=2_600,
            text="第二条",
            tts_text="第二条",
            source_cue_ids=["cue-2"],
            source_word_ids=["word-2"],
            paragraph_id="paragraph-2",
            quality_flags=[],
        ),
    ]
    dual_tracks = SimpleNamespace(
        spoken_segments=[],
        display_cues=display_cues,
    )

    subtitles = localization_tracks._formal_subtitles(
        dual_tracks,
        frame_rate=25.0,
        media_duration_ms=3_000,
    )

    assert [item.start_ms for item in subtitles] == [1_000, 2_000]
    assert [item.end_ms for item in subtitles] == [1_800, 3_000]
    assert all(
        "timing:display-exit-extended" in item.quality_flags
        for item in subtitles
    )


def test_tracks_quality_gate_warns_for_adjacent_duplicate_chinese_sentence():
    script, adjudicated = _fixture()
    sections = list(script.content.sections)
    first = sections[0]
    paragraphs = list(first.paragraphs)
    paragraphs[0] = (
        "先看第一个效果，它已经成功完成。"
        "先看第一个效果，它已经成功完成。"
    )
    sections[0] = first.model_copy(update={"paragraphs": paragraphs})
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": sections}
            ),
            "result_fingerprint": "c" * 64,
        }
    )
    adjudicated = adjudicated.model_copy(
        update={"spoken_script_fingerprint": script.result_fingerprint}
    )
    dual = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    result = localization_tracks.validate_localization_tracks(
        localization_tracks.LocalizationTracksQualityGateInput(
            dual_tracks_operation_id="dual_tracks_operation",
            source_fingerprint=script.source_fingerprint,
            final_script=script,
            dual_tracks=dual,
        ),
        current_source_fingerprint=script.source_fingerprint,
    )

    assert result.decision == "warning"
    assert "duplicate_chinese_sentence" in {
        item.code for item in result.warnings
    }


def test_tracks_quality_gate_warns_for_repeated_short_dialogue_turns():
    script, adjudicated = _fixture()
    sections = list(script.content.sections)
    first = sections[0]
    paragraphs = list(first.paragraphs)
    paragraphs[:2] = ["“拉钩。”", "“拉钩。”"]
    sections[0] = first.model_copy(update={"paragraphs": paragraphs})
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": sections}
            ),
            "result_fingerprint": "d" * 64,
        }
    )
    adjudicated = adjudicated.model_copy(
        update={"spoken_script_fingerprint": script.result_fingerprint}
    )
    dual = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    result = localization_tracks.validate_localization_tracks(
        localization_tracks.LocalizationTracksQualityGateInput(
            dual_tracks_operation_id="dual_tracks_operation",
            source_fingerprint=script.source_fingerprint,
            final_script=script,
            dual_tracks=dual,
        ),
        current_source_fingerprint=script.source_fingerprint,
    )

    assert result.decision == "warning"
    assert not result.blockers
    assert [(item.code, item.count) for item in result.warnings] == [
        ("repeated_dialogue_turn", 1)
    ]


def test_tracks_quality_gate_aggregates_repeated_issue_rows():
    issue = localization_tracks.LocalizationTracksQualityIssue(
        code="manual_review",
        message="建议人工核对",
        severity="warning",
    )

    aggregated = localization_tracks._aggregate_quality_issues(
        [issue, issue, issue.model_copy(update={"count": 2})]
    )

    assert len(aggregated) == 1
    assert aggregated[0].count == 4


def test_formal_display_subtitles_store_independent_tts_pronunciation_text():
    script, adjudicated = _fixture()
    section = script.content.sections[0].model_copy(
        update={
            "paragraphs": [
                "Seedance 2.0 是 AI 视频模型，支持 4K。",
                "然后进入第二件事，最后收住。",
            ]
        }
    )
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": [section]}
            ),
            "result_fingerprint": "e" * 64,
        }
    )
    adjudicated = adjudicated.model_copy(
        update={"spoken_script_fingerprint": script.result_fingerprint}
    )
    dual = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    subtitles = localization_tracks._formal_subtitles(dual)

    assert subtitles[0].text == "Seedance 2.0 是 AI 视频模型 支持 4K"
    assert subtitles[0].tts_text == (
        "Seedance 二点零是 A I 视频模型，支持四 K。"
    )


def test_formal_localization_refresh_preserves_existing_dub_timeline_media(
    monkeypatch,
):
    script, adjudicated = _fixture()
    adjudicated = adjudicated.model_copy(
        update={
            "blocks": [
                block.model_copy(
                    update={
                        "source_start_ms": index * 4_000,
                        "source_end_ms": (index + 1) * 4_000,
                    }
                )
                for index, block in enumerate(adjudicated.blocks)
            ]
        }
    )
    dual = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )
    gate = localization_tracks.validate_localization_tracks(
        localization_tracks.LocalizationTracksQualityGateInput(
            dual_tracks_operation_id="dual_tracks_operation",
            source_fingerprint=script.source_fingerprint,
            final_script=script,
            dual_tracks=dual,
        ),
        current_source_fingerprint=script.source_fingerprint,
    )
    draft = _draft().model_copy(
        update={
            "localized_subtitles": [
                localization_tracks.VideoLocalizationSubtitleCue(
                    subtitle_id="localized_old",
                    start_ms=100,
                    end_ms=900,
                    text="旧字幕",
                    tts_text="旧配音台词",
                    source_cue_ids=["cue_0001"],
                )
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_keep",
                    "track_id": "dub",
                    "subtitle_id": "localized_old",
                    "target_subtitle_ids": ["localized_old"],
                    "start_ms": 321,
                    "end_ms": 987,
                    "source_start_ms": 111,
                    "source_end_ms": 777,
                    "dub_lane": 2,
                    "audio_path": "managed-old.wav",
                    "result_id": "result_old",
                    "status": "ready",
                },
                {
                    "clip_id": "background",
                    "track_id": "background",
                    "start_ms": 0,
                    "end_ms": 2_000,
                },
            ],
        }
    )
    monkeypatch.setattr(
        localization_tracks.localization_source.DEFAULT_LOCALIZATION_PIPELINE,
        "lock_source",
        lambda _request: SimpleNamespace(
            source_fingerprint=script.source_fingerprint
        ),
    )

    updated, result = localization_tracks.build_formal_localization_dual_tracks(
        draft,
        final_script=script,
        dual_tracks=dual,
        gate=gate,
    )

    assert [item["clip_id"] for item in updated.timeline_clips] == [
        "clip_keep",
        "background",
    ]
    assert updated.timeline_clips[0] == {
        **draft.timeline_clips[0],
        "tts_target_binding_status": "stale",
        "tts_target_text": "旧配音台词",
    }
    assert updated.timeline_clips[1] == draft.timeline_clips[1]
    assert result.detached_dub_clip_count == 1
