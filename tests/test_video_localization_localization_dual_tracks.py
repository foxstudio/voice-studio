from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_alignment_adjudication,
    localization_dual_tracks,
    localization_semantic_alignment,
    localization_spoken_script,
    subtitle_punctuation,
)
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)
from app.domains.video_localization.localization_source import (  # noqa: E402
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (  # noqa: E402
    SourceBoundaryEvidence,
)
from tests.test_video_localization_localization_semantic_alignment_v3 import (  # noqa: E402
    _FixtureEncoder,
    _request,
)


def _source_cues_with_speakers(*speaker_ids: str):
    request = _request(
        [
            ("paragraph_0001", "先说第一件事。这里自然接着讲。"),
            ("paragraph_0002", "然后进入第二件事，最后收住。"),
        ],
        [
            ("cue_0001", "first source"),
            ("cue_0002", "first detail"),
            ("cue_0003", "second source"),
        ],
    )
    return [
        cue.model_copy(update={"speaker_id": speaker_id})
        for cue, speaker_id in zip(request.source_cues, speaker_ids)
    ]


def _fixture():
    script_fingerprint = "b" * 64
    alignment_request = _request(
        [
            ("paragraph_0001", "先说第一件事。这里自然接着讲。"),
            ("paragraph_0002", "然后进入第二件事，最后收住。"),
        ],
        [
            ("cue_0001", "first source"),
            ("cue_0002", "first detail"),
            ("cue_0003", "second source"),
        ],
    ).model_copy(
        update={"spoken_script_fingerprint": script_fingerprint}
    )
    encoder = _FixtureEncoder(
        {
            "先说第一件事。这里自然接着讲。": [1.0, 0.0],
            "然后进入第二件事，最后收住。": [0.0, 1.0],
            "first source": [1.0, 0.0],
            "first detail": [0.95, 0.05],
            "second source": [0.0, 1.0],
        }
    )
    alignment = localization_semantic_alignment.align_localized_script(
        alignment_request,
        encoder=encoder,
    )
    route = LocalizationAiPhaseRoute(
        phase="alignment_adjudication",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    adjudicated = (
        localization_alignment_adjudication
        .adjudicate_localization_alignment(
            localization_alignment_adjudication
            .LocalizationAlignmentAdjudicationInput(
                alignment_operation_id="alignment_operation",
                alignment=alignment.model_copy(
                    update={
                        "blocks": [
                            item.model_copy(
                                update={"needs_adjudication": False}
                            )
                            for item in alignment.blocks
                        ]
                    }
                ),
                source_cues=alignment_request.source_cues,
                target_paragraphs=alignment_request.target_paragraphs,
                route=route,
            )
        )
    )
    script = (
        localization_spoken_script.LocalizationSpokenScriptFinalResult
        .model_construct(
            source_fingerprint="a" * 64,
            input_script_fingerprint="c" * 64,
            result_fingerprint=script_fingerprint,
            content=(
                localization_spoken_script
                .LocalizationSpokenScriptContent(
                    title="测试",
                    sections=[
                        localization_spoken_script
                        .LocalizationSpokenScriptSection(
                            section_id="section_0001",
                            heading="正文",
                            paragraphs=[
                                "先说第一件事。这里自然接着讲。",
                                "然后进入第二件事，最后收住。",
                            ],
                        )
                    ],
                )
            ),
            route=route,
            revised_section_ids=[],
            llm_calls=[],
            quality_summary=(
                localization_spoken_script
                .LocalizationSpokenScriptFinalQualitySummary(
                    status="passed",
                    source_issue_count=0,
                    revised_section_count=0,
                    unchanged_section_count=1,
                    section_coverage_complete=True,
                    model_call_count=0,
                )
            ),
        )
    )
    return script, adjudicated


def test_dual_tracks_preserve_spoken_text_and_build_display_cues():
    script, adjudicated = _fixture()
    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    assert [item.tts_text for item in result.spoken_segments] == [
        "先说第一件事。这里自然接着讲。",
        "然后进入第二件事，最后收住。",
    ]
    assert "".join(
        item.text.replace("\n", "") for item in result.display_cues
    ) == (
        "先说第一件事这里自然接着讲"
        "然后进入第二件事 最后收住"
    )
    assert [item.tts_text for item in result.spoken_segments] == [
        "先说第一件事。这里自然接着讲。",
        "然后进入第二件事，最后收住。",
    ]
    assert result.quality_summary.paragraph_coverage_complete is True
    assert result.quality_summary.display_timing_ordered is True
    assert result.quality_summary.model_call_count == 0


def test_dual_tracks_reject_mixed_speaker_spoken_segment():
    script, adjudicated = _fixture()

    with pytest.raises(
        ValueError,
        match="同一中文台词段不能跨说话人",
    ):
        localization_dual_tracks.build_localization_dual_tracks(
            localization_dual_tracks.LocalizationDualTrackInput(
                spoken_script_operation_id="script_operation",
                alignment_operation_id="alignment_operation",
                spoken_script=script,
                alignment=adjudicated,
                source_cues=_source_cues_with_speakers(
                    "speaker_01",
                    "speaker_02",
                    "speaker_02",
                ),
            )
        )


def test_dual_tracks_keep_single_speaker_tutorial_unchanged():
    script, adjudicated = _fixture()

    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
            source_cues=_source_cues_with_speakers(
                "speaker_01",
                "speaker_01",
                "speaker_01",
            ),
        )
    )

    assert [item.tts_text for item in result.spoken_segments] == [
        "先说第一件事。这里自然接着讲。",
        "然后进入第二件事，最后收住。",
    ]


def test_dual_tracks_reuse_the_alignment_paragraph_units():
    script, _ = _fixture()
    section = script.content.sections[0].model_copy(
        update={
            "paragraphs": [
                "原台词变成：",
                "“零钱？……没有。”",
                "下一段。",
            ]
        }
    )
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": [section]}
            )
        }
    )

    assert localization_dual_tracks._script_paragraphs(script) == [
        ("paragraph_0001", "原台词变成： “零钱？……没有。”"),
        ("paragraph_0002", "下一段。"),
    ]


def test_grouped_paragraph_windows_cover_the_complete_semantic_window():
    script, adjudicated = _fixture()
    section = script.content.sections[0].model_copy(
        update={
            "paragraphs": [
                "好。",
                "然后完整说明第二件事为什么重要，以及接下来具体应该怎么做。",
            ]
        }
    )
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": [section]}
            ),
            "result_fingerprint": "d" * 64,
        }
    )
    source_blocks = adjudicated.blocks
    grouped = source_blocks[0].model_copy(
        update={
            "paragraph_ids": ["paragraph_0001", "paragraph_0002"],
            "source_cue_ids": [
                cue_id
                for block in source_blocks
                for cue_id in block.source_cue_ids
            ],
            "source_word_ids": [
                word_id
                for block in source_blocks
                for word_id in block.source_word_ids
            ],
            "source_start_ms": 0,
            "source_end_ms": 5_000,
        }
    )
    adjudicated = adjudicated.model_copy(
        update={
            "spoken_script_fingerprint": script.result_fingerprint,
            "blocks": [grouped],
        }
    )

    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    paragraph_windows = {
        item.paragraph_id: (
            item.semantic_start_ms,
            item.semantic_end_ms,
        )
        for item in result.spoken_segments
    }
    assert paragraph_windows["paragraph_0001"][0] == 0
    assert paragraph_windows["paragraph_0002"][1] == 5_000
    assert result.display_cues[0].start_ms == 0
    assert result.display_cues[-1].end_ms == 5_000


def test_grouped_paragraphs_partition_source_words_before_display_splitting():
    script, adjudicated = _fixture()
    first_paragraph = (
        "现在把原始素材和完整提示词一起提交到 Seedance，看看生成结果。"
    )
    second_paragraph = "这光线也太夸张了。"
    section = script.content.sections[0].model_copy(
        update={"paragraphs": [first_paragraph, second_paragraph]}
    )
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": [section]}
            ),
            "result_fingerprint": "f" * 64,
        }
    )
    source_words = [
        LocalizationSourceWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, text in enumerate(
            ["Now", "submit", "Seedance", "see", "result.", "Amazing."],
            start=1,
        )
    ]
    source_words[0] = source_words[0].model_copy(
        update={"display_entry_ms": 80}
    )
    source_cues = [
        localization_semantic_alignment.LocalizationAlignmentSourceCue(
            cue_id="cue_0001",
            text="Now submit Seedance",
            start_ms=0,
            end_ms=3_000,
            source_word_ids=[
                "word_0001",
                "word_0002",
                "word_0003",
            ],
        ),
        localization_semantic_alignment.LocalizationAlignmentSourceCue(
            cue_id="cue_0002",
            text="see result.",
            start_ms=3_000,
            end_ms=5_000,
            source_word_ids=["word_0004", "word_0005"],
        ),
        localization_semantic_alignment.LocalizationAlignmentSourceCue(
            cue_id="cue_0003",
            text="Amazing.",
            start_ms=5_000,
            end_ms=6_000,
            source_word_ids=["word_0006"],
        ),
    ]
    grouped = adjudicated.blocks[0].model_copy(
        update={
            "paragraph_ids": ["paragraph_0001", "paragraph_0002"],
            "source_cue_ids": [item.cue_id for item in source_cues],
            "source_word_ids": [item.word_id for item in source_words],
            "source_start_ms": 0,
            "source_end_ms": 6_000,
        }
    )
    adjudicated = adjudicated.model_copy(
        update={
            "spoken_script_fingerprint": script.result_fingerprint,
            "blocks": [grouped],
        }
    )

    class _GroupedEncoder:
        model_id = localization_semantic_alignment.MODEL_ID
        model_fingerprint = "fixture-grouped-paragraphs-v1"

        def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for text in texts:
                normalized = text.lower()
                rows.append(
                    [
                        float(
                            any(
                                token in normalized
                                for token in (
                                    "现在",
                                    "原始素材",
                                    "完整提示词",
                                    "提交",
                                    "now",
                                    "submit",
                                    "seedance",
                                )
                            )
                        ),
                        float(
                            any(
                                token in normalized
                                for token in (
                                    "看看",
                                    "结果",
                                    "see",
                                    "result",
                                )
                            )
                        ),
                        float(
                            any(
                                token in normalized
                                for token in (
                                    "光线",
                                    "夸张",
                                    "amazing",
                                )
                            )
                        ),
                    ]
                )
            values = np.asarray(rows, dtype=np.float32)
            return values / np.maximum(
                np.linalg.norm(values, axis=1, keepdims=True),
                1e-12,
            )

    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
            source_cues=source_cues,
            source_words=source_words,
        ),
        encoder=_GroupedEncoder(),
    )

    first_paragraph_cues = [
        item
        for item in result.display_cues
        if item.paragraph_id == "paragraph_0001"
    ]
    second_paragraph_cues = [
        item
        for item in result.display_cues
        if item.paragraph_id == "paragraph_0002"
    ]
    assert len(first_paragraph_cues) == 2
    assert result.spoken_segments[0].semantic_start_ms == 0
    assert first_paragraph_cues[0].start_ms == 80
    assert first_paragraph_cues[-1].end_ms <= second_paragraph_cues[0].start_ms
    assert set(first_paragraph_cues[-1].source_word_ids).isdisjoint(
        second_paragraph_cues[0].source_word_ids
    )
    assert second_paragraph_cues[0].source_word_ids == ["word_0006"]


def test_short_semantic_window_is_not_extended_by_reading_speed_rules():
    script, adjudicated = _fixture()
    section = script.content.sections[0].model_copy(
        update={"paragraphs": ["看效果。", "接下来继续说明完整结果。"]}
    )
    script = script.model_copy(
        update={
            "content": script.content.model_copy(
                update={"sections": [section]}
            ),
            "result_fingerprint": "e" * 64,
        }
    )
    blocks = [
        adjudicated.blocks[0].model_copy(
            update={
                "paragraph_ids": ["paragraph_0001"],
                "source_start_ms": 1_000,
                "source_end_ms": 1_680,
            }
        ),
        adjudicated.blocks[1].model_copy(
            update={
                "paragraph_ids": ["paragraph_0002"],
                "source_start_ms": 2_000,
                "source_end_ms": 5_000,
            }
        ),
    ]
    adjudicated = adjudicated.model_copy(
        update={
            "spoken_script_fingerprint": script.result_fingerprint,
            "blocks": blocks,
        }
    )

    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    first, second = result.display_cues
    assert (first.start_ms, first.end_ms) == (1_000, 1_680)
    assert first.end_ms <= second.start_ms
    assert not {
        "display_duration_short",
        "display_reading_speed_high",
    }.intersection(first.quality_flags)


def test_display_cards_cover_the_exact_semantic_start_and_end():
    script, adjudicated = _fixture()
    result = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script_operation",
            alignment_operation_id="alignment_operation",
            spoken_script=script,
            alignment=adjudicated,
        )
    )

    spoken_by_paragraph = {
        item.paragraph_id: item for item in result.spoken_segments
    }
    display_by_paragraph = {
        paragraph_id: [
            item
            for item in result.display_cues
            if item.paragraph_id == paragraph_id
        ]
        for paragraph_id in spoken_by_paragraph
    }
    for paragraph_id, spoken in spoken_by_paragraph.items():
        displays = display_by_paragraph[paragraph_id]
        assert (displays[0].start_ms, displays[-1].end_ms) == (
            spoken.semantic_start_ms,
            spoken.semantic_end_ms,
        )


def test_dual_track_policy_has_no_reading_speed_or_duration_controls():
    assert localization_dual_tracks.LocalizationDualTrackPolicy().model_dump() == {
        "maximum_display_characters": 27,
        "strong_source_pause_ms": 1_200,
    }


def test_display_split_prefers_punctuation_and_does_not_hard_cut_words():
    parts = localization_dual_tracks.split_localization_display_text(
        "先保留 Seedance 2.0 这个完整名称，再继续讲下一件事。",
        maximum_characters=16,
    )

    assert "Seedance 2.0" in "".join(parts)
    assert "".join(parts) == (
        "先保留 Seedance 2.0 这个完整名称，再继续讲下一件事。"
    )


def test_display_split_uses_release_width_for_mixed_language_model_name():
    parts = localization_dual_tracks.split_localization_display_text(
        "用的是 Higgsfield 里的 Seedance 2.0 in 4K，",
        maximum_characters=28,
    )

    assert parts == ["用的是 Higgsfield 里的 Seedance 2.0 in 4K，"]


def test_display_punctuation_uses_the_shared_subtitle_policy():
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(
            "先说一句，继续。真的吗？！Seedance 2.0... A、B、C。"
        )
        == "先说一句 继续 真的吗 Seedance 2.0 A B C"
    )


def test_display_split_uses_chinese_pause_before_hard_cutting_tail_word():
    parts = localization_dual_tracks.split_localization_display_text(
        (
            "你给它一段原始视频，再用一句话说清楚想要什么，"
            "它就能给你一条完成度足够高、"
            "可以直接放进实际项目里的成片。"
        ),
        maximum_characters=27,
    )

    assert "".join(parts) == (
        "你给它一段原始视频，再用一句话说清楚想要什么，"
        "它就能给你一条完成度足够高、"
        "可以直接放进实际项目里的成片。"
    )
    assert all(part not in {"片。", "成片。"} for part in parts)
    assert not any(
        left.endswith("成") and right.startswith("片")
        for left, right in zip(parts, parts[1:])
    )


def test_display_split_keeps_short_reaction_after_a_full_sentence_separate():
    parts = localization_dual_tracks.split_localization_display_text(
        "我可以在身后放进一个现实中绝对不该出现的东西。离谱吧？",
        maximum_characters=27,
    )

    assert parts == [
        "我可以在身后放进一个现实中绝对不该出现的东西。",
        "离谱吧？",
    ]


def test_display_split_keeps_complete_sentences_apart_across_strong_source_pause():
    parts = localization_dual_tracks.split_localization_display_text(
        "再跑四个批次。好，还不差，但得更好玩、更有意思一点。",
        maximum_characters=27,
        preserve_sentence_boundaries=True,
    )

    assert parts == [
        "再跑四个批次。",
        "好，还不差，但得更好玩、更有意思一点。",
    ]


def test_display_split_keeps_closing_quote_with_quoted_question():
    parts = localization_dual_tracks.split_localization_display_text(
        "我让 Claude 帮我扩展这个点子，它就问：‘片长多少？’",
        maximum_characters=27,
        preserve_sentence_boundaries=True,
    )

    assert "".join(parts) == (
        "我让 Claude 帮我扩展这个点子，它就问：‘片长多少？’"
    )
    assert all(part not in {"’", "”", "」", "』"} for part in parts)
    assert all(
        localization_dual_tracks._display_tts_text(part)
        for part in parts
    )


def test_display_cards_coalesce_when_source_has_fewer_unique_words():
    parts = (
        localization_dual_tracks
        ._coalesce_display_parts_to_source_capacity(
            [
                "第一步，剧本。",
                "好，我们先从故事本身讲起，看看剧本是怎么写出来的。",
            ],
            source_word_capacity=1,
        )
    )

    assert parts == [
        "第一步，剧本。好，我们先从故事本身讲起，看看剧本是怎么写出来的。"
    ]


def test_display_source_span_marks_objective_strong_pause_for_manual_review():
    boundary = SourceBoundaryEvidence(
        boundary_id="word_0001:word_0002",
        left_word_id="word_0001",
        right_word_id="word_0002",
        left_cue_id="cue_0001",
        right_cue_id="cue_0002",
        position_after_word=1,
        cue_boundary=True,
        eligible=True,
        punctuation="clause",
        pause_ms=16_000,
        pause_confidence="high",
        segment_change=True,
        speaker_change=False,
        overlap_speech=False,
        objective_support=True,
        classification="hard",
        score=20,
        reason_codes=["confirmed_pause", "cue_boundary"],
    )

    assert localization_dual_tracks.source_word_ids_span_strong_pause(
        ["word_0001", "word_0002"],
        evidence_by_left_word={"word_0001": boundary},
        minimum_pause_ms=1_200,
    ) is True


def test_display_split_never_merges_separate_dialogue_lines():
    parts = localization_dual_tracks.split_localization_display_text(
        "“我的天！”\n“真的是你吗？”",
        maximum_characters=27,
    )

    assert parts == ["“我的天！”", "“真的是你吗？”"]
    assert localization_dual_tracks._display_tts_text(parts[1]) == (
        "真的是你吗？"
    )


def test_display_split_merges_tiny_connective_into_following_sentence():
    parts = localization_dual_tracks.split_localization_display_text(
        "好，现在把原始素材和这条提示词一起交给 Seedance，",
        maximum_characters=27,
    )

    assert parts == [
        "好，现在把原始素材和这条提示词一起交给 Seedance，",
    ]
    assert parts[0] != "好，"


def test_display_split_prefers_semantic_dash_and_never_breaks_jingtou():
    text = (
        "以及我周围的全部混乱——"
        "都被融合进了这一个持续运动的镜头里。"
    )

    parts = localization_dual_tracks.split_localization_display_text(
        text,
        maximum_characters=20,
    )

    assert "".join(parts) == text
    assert parts == [
        "以及我周围的全部混乱——",
        "都被融合进了这一个持续运动的镜头里。",
    ]
    assert [
        subtitle_punctuation.normalize_display_subtitle_punctuation(part)
        for part in parts
    ] == [
        "以及我周围的全部混乱",
        "都被融合进了这一个持续运动的镜头里",
    ]
    assert not any(
        left.endswith("镜") and right.startswith("头")
        for left, right in zip(parts, parts[1:])
    )


def test_display_split_keeps_oversized_chinese_clause_whole_without_boundary():
    text = "这是一个没有可靠语义边界而且不应该按固定字数强行切开的完整句子"

    parts = localization_dual_tracks.split_localization_display_text(
        text,
        maximum_characters=12,
    )

    assert parts == [text]


def test_display_card_keeps_backend_text_unbroken_for_viewport_layout():
    value = localization_dual_tracks.format_localization_display_lines(
        "这里保留完整的 Seedance 2.0 名称再继续说明效果"
    )

    assert "\n" not in value
    assert "Seedance 2.0" in value


def test_display_card_never_hard_splits_a_complete_chinese_word():
    value = localization_dual_tracks.format_localization_display_lines(
        "我可以让自己的脑袋着火，嘴里还照常说话"
    )

    assert value == "我可以让自己的脑袋着火，嘴里还照常说话"
    assert "脑袋着\n火" not in value


def test_display_cards_use_semantic_word_boundary_and_pause_not_character_ratio():
    words = [
        LocalizationSourceWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=start,
            end_ms=end,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, (text, start, end) in enumerate(
            [
                ("I", 22_080, 22_180),
                ("can", 22_200, 22_400),
                ("put", 22_420, 22_650),
                ("something", 22_680, 23_200),
                ("behind", 23_240, 23_600),
                ("me", 23_620, 23_820),
                ("that", 23_840, 24_000),
                ("shouldn't", 24_020, 24_400),
                ("be", 24_420, 24_520),
                ("there.", 24_540, 25_080),
                ("Insane", 26_360, 26_760),
                ("right?", 26_780, 27_080),
            ],
            start=1,
        )
    ]
    boundary = SourceBoundaryEvidence(
        boundary_id="word_0010:word_0011",
        left_word_id="word_0010",
        right_word_id="word_0011",
        left_cue_id="cue_0001",
        right_cue_id="cue_0002",
        position_after_word=10,
        cue_boundary=True,
        eligible=True,
        punctuation="terminal",
        pause_ms=1_280,
        pause_confidence="high",
        segment_change=True,
        speaker_change=False,
        overlap_speech=False,
        objective_support=True,
        classification="preferred",
        score=16,
        reason_codes=[
            "cue_boundary",
            "terminal_punctuation",
            "confirmed_pause",
            "segment_change",
        ],
    )

    class _DisplayEncoder:
        model_id = localization_semantic_alignment.MODEL_ID
        model_fingerprint = "fixture-display-alignment-v1"

        def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for text in texts:
                if text.startswith("我可以"):
                    rows.append([1.0, 0.0])
                elif text.startswith("离谱"):
                    rows.append([0.0, 1.0])
                elif text.endswith("there."):
                    rows.append([1.0, 0.0])
                elif text.startswith("Insane"):
                    rows.append([0.0, 1.0])
                else:
                    rows.append([0.5, 0.5])
            values = np.asarray(rows, dtype=np.float32)
            return values / np.maximum(
                np.linalg.norm(values, axis=1, keepdims=True),
                1e-12,
            )

    planned = localization_dual_tracks._plan_display_windows(
        [
            "我可以在身后放进一个现实中绝对不该出现的东西。",
            "离谱吧？",
        ],
        window=localization_dual_tracks.LocalizationParagraphWindow(
            paragraph_id="paragraph_0001",
            text="我可以在身后放进一个现实中绝对不该出现的东西。离谱吧？",
            start_ms=22_080,
            end_ms=27_080,
            source_cue_ids=["cue_0001", "cue_0002"],
            source_word_ids=[item.word_id for item in words],
        ),
        word_by_id={item.word_id: item for item in words},
        cue_by_word={
            item.word_id: (
                "cue_0001" if index < 10 else "cue_0002"
            )
            for index, item in enumerate(words)
        },
        evidence_by_left_word={boundary.left_word_id: boundary},
        baseline_density=8.0,
        encoder=_DisplayEncoder(),
    )

    assert planned[0][1:3] == (22_080, 25_080)
    assert planned[1][1:3] == (26_360, 27_080)
    assert planned[0][4][-1] == "word_0010"
    assert planned[1][4] == ["word_0011", "word_0012"]


def test_display_cards_never_detach_timing_from_owned_source_words():
    words = [
        LocalizationSourceWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=start,
            end_ms=end,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, (text, start, end) in enumerate(
            [
                ("one", 0, 80),
                ("two", 80, 400),
                ("three", 400, 700),
                ("four", 700, 1_000),
            ],
            start=1,
        )
    ]
    boundary = SourceBoundaryEvidence(
        boundary_id="word_0001:word_0002",
        left_word_id="word_0001",
        right_word_id="word_0002",
        left_cue_id="cue_0001",
        right_cue_id="cue_0001",
        position_after_word=1,
        cue_boundary=False,
        eligible=True,
        punctuation="none",
        pause_ms=0,
        pause_confidence="low",
        segment_change=False,
        speaker_change=False,
        overlap_speech=False,
        objective_support=False,
        classification="ambiguous",
        score=0,
        reason_codes=[],
    )

    class _ExtremeSplitEncoder:
        model_id = localization_semantic_alignment.MODEL_ID
        model_fingerprint = "fixture-extreme-split-v1"

        def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for text in texts:
                if text in {"前半部分需要正常显示", "one"}:
                    rows.append([1.0, 0.0])
                elif text in {"后半部分也需要正常显示", "two three four"}:
                    rows.append([0.0, 1.0])
                elif text == "one two":
                    rows.append([0.0, 1.0])
                elif text == "three four":
                    rows.append([1.0, 0.0])
                else:
                    rows.append([0.5, 0.5])
            values = np.asarray(rows, dtype=np.float32)
            return values / np.maximum(
                np.linalg.norm(values, axis=1, keepdims=True),
                1e-12,
            )

    planned = localization_dual_tracks._plan_display_windows(
        ["前半部分需要正常显示", "后半部分也需要正常显示"],
        window=localization_dual_tracks.LocalizationParagraphWindow(
            paragraph_id="paragraph_0001",
            text="前半部分需要正常显示。后半部分也需要正常显示。",
            start_ms=0,
            end_ms=1_000,
            source_cue_ids=["cue_0001"],
            source_word_ids=[item.word_id for item in words],
        ),
        word_by_id={item.word_id: item for item in words},
        cue_by_word={item.word_id: "cue_0001" for item in words},
        evidence_by_left_word={boundary.left_word_id: boundary},
        baseline_density=20.0,
        encoder=_ExtremeSplitEncoder(),
    )

    assert planned[0][1:3] == (0, 80)
    assert planned[1][1:3] == (80, 1_000)
    assert planned[0][4] == ["word_0001"]
    assert planned[1][4] == [
        "word_0002",
        "word_0003",
        "word_0004",
    ]


def test_display_cards_choose_one_joint_semantic_path_instead_of_cascading():
    words = [
        LocalizationSourceWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, text in enumerate(
            ["alpha", "detail", "beta", "detail", "gamma", "detail"],
            start=1,
        )
    ]
    evidence = {
        words[position - 1].word_id: SourceBoundaryEvidence(
            boundary_id=(
                f"{words[position - 1].word_id}:"
                f"{words[position].word_id}"
            ),
            left_word_id=words[position - 1].word_id,
            right_word_id=words[position].word_id,
            left_cue_id="cue_0001",
            right_cue_id="cue_0001",
            position_after_word=position,
            cue_boundary=False,
            eligible=True,
            punctuation="none",
            pause_ms=0,
            pause_confidence="low",
            segment_change=False,
            speaker_change=False,
            overlap_speech=False,
            objective_support=False,
            classification="ambiguous",
            score=0,
            reason_codes=[],
        )
        for position in range(1, len(words))
    }

    class _CascadeEncoder:
        model_id = localization_semantic_alignment.MODEL_ID
        model_fingerprint = "fixture-joint-display-path-v1"

        def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for text in texts:
                normalized = " ".join(text.split())
                if normalized in {"甲", "alpha detail"}:
                    rows.append([1.0, 0.0, 0.0])
                elif normalized in {"乙", "beta detail"}:
                    rows.append([0.0, 1.0, 0.0])
                elif normalized in {"丙", "gamma detail"}:
                    rows.append([0.0, 0.0, 1.0])
                elif normalized == "乙丙":
                    rows.append([0.0, 1.0, 1.0])
                elif normalized == "alpha detail beta detail":
                    # A left-to-right greedy split is tempted to consume the
                    # second card because its remaining-text score looks good.
                    rows.append([1.0, 0.0, 0.0])
                elif normalized == "beta detail gamma detail":
                    rows.append([1.0, 0.0, 0.0])
                elif normalized == "gamma detail":
                    rows.append([0.0, 0.0, 1.0])
                else:
                    rows.append([0.2, 0.2, 0.2])
            values = np.asarray(rows, dtype=np.float32)
            return values / np.maximum(
                np.linalg.norm(values, axis=1, keepdims=True),
                1e-12,
            )

    planned = localization_dual_tracks._plan_display_windows(
        ["甲", "乙", "丙"],
        window=localization_dual_tracks.LocalizationParagraphWindow(
            paragraph_id="paragraph_0001",
            text="甲乙丙",
            start_ms=0,
            end_ms=6_000,
            source_cue_ids=["cue_0001"],
            source_word_ids=[item.word_id for item in words],
        ),
        word_by_id={item.word_id: item for item in words},
        cue_by_word={item.word_id: "cue_0001" for item in words},
        evidence_by_left_word=evidence,
        baseline_density=0.5,
        encoder=_CascadeEncoder(),
    )

    assert [item[4] for item in planned] == [
        ["word_0001", "word_0002"],
        ["word_0003", "word_0004"],
        ["word_0005", "word_0006"],
    ]


def test_display_entry_never_moves_before_allocated_paragraph_window():
    word = LocalizationSourceWord(
        word_id="word_0001",
        segment_id="segment_0001",
        text="Ah!",
        start_ms=100,
        end_ms=900,
        display_entry_ms=100,
        timing_confidence="high",
        timing_source="forced_aligner",
    )

    planned = localization_dual_tracks._plan_display_windows(
        ["啊！"],
        window=localization_dual_tracks.LocalizationParagraphWindow(
            paragraph_id="paragraph_0002",
            text="啊！",
            start_ms=500,
            end_ms=1_000,
            source_cue_ids=["cue_0001"],
            source_word_ids=[word.word_id],
        ),
        word_by_id={word.word_id: word},
        cue_by_word={word.word_id: "cue_0001"},
        evidence_by_left_word={},
        baseline_density=1.0,
        encoder=None,
    )

    assert planned[0][1:3] == (500, 1_000)
