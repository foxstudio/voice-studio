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
    localization_semantic_alignment,
)
from app.domains.video_localization.localization_source import (  # noqa: E402
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (  # noqa: E402
    SourceBoundaryEvidence,
)


class _FixtureEncoder:
    model_id = localization_semantic_alignment.MODEL_ID
    model_fingerprint = "fixture-labse-v1"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def encode(self, texts: list[str]) -> np.ndarray:
        rows = np.asarray(
            [self.vectors[text] for text in texts],
            dtype=np.float32,
        )
        norms = np.linalg.norm(rows, axis=1, keepdims=True)
        return rows / np.maximum(norms, 1e-12)


def _request(
    targets: list[tuple[str, str]],
    sources: list[tuple[str, str]],
    *,
    source_speakers: list[str | None] | None = None,
) -> localization_semantic_alignment.LocalizationSemanticAlignmentInput:
    if source_speakers is not None:
        assert len(source_speakers) == len(sources)
    return (
        localization_semantic_alignment
        .LocalizationSemanticAlignmentInput(
            source_fingerprint="a" * 64,
            spoken_script_fingerprint="b" * 64,
            source_cues=[
                localization_semantic_alignment
                .LocalizationAlignmentSourceCue(
                    cue_id=cue_id,
                    text=text,
                    start_ms=index * 1_000,
                    end_ms=(index + 1) * 1_000,
                    source_word_ids=[f"word_{index:04d}"],
                    speaker_id=(
                        source_speakers[index]
                        if source_speakers is not None
                        else None
                    ),
                )
                for index, (cue_id, text) in enumerate(sources)
            ],
            target_paragraphs=[
                localization_semantic_alignment
                .LocalizationAlignmentTargetParagraph(
                    paragraph_id=paragraph_id,
                    text=text,
                )
                for paragraph_id, text in targets
            ],
        )
    )


def test_semantic_alignment_never_maps_one_paragraph_across_speaker_turns():
    request = _request(
        [
            ("paragraph_0001", "中文提问"),
            ("paragraph_0002", "中文回答"),
        ],
        [
            ("cue_0001", "question lead"),
            ("cue_0002", "question detail"),
            ("cue_0003", "answer lead"),
            ("cue_0004", "answer detail"),
        ],
        source_speakers=[
            "speaker_01",
            "speaker_01",
            "speaker_02",
            "speaker_02",
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文提问": [1.0, 0.0],
            "中文回答": [0.0, 1.0],
            "question lead": [1.0, 0.0],
            "question detail": [1.0, 0.0],
            # This tempting semantic match used to make the first target
            # consume the start of the next person's answer.
            "answer lead": [1.0, 0.0],
            "answer detail": [0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert [block.source_cue_ids for block in result.blocks] == [
        ["cue_0001", "cue_0002"],
        ["cue_0003", "cue_0004"],
    ]


def test_alignment_keeps_colon_lead_in_with_the_content_it_introduces():
    paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
        [
            "前一段。",
            "于是原台词变成：",
            "“零钱？……没有。”",
            "后一段。",
        ]
    )

    assert [item.text for item in paragraphs] == [
        "前一段。",
        "于是原台词变成： “零钱？……没有。”",
        "后一段。",
    ]
    assert [item.paragraph_id for item in paragraphs] == [
        "paragraph_0001",
        "paragraph_0002",
        "paragraph_0003",
    ]


def test_alignment_keeps_consecutive_introduced_phrases_as_one_comparison():
    paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
        [
            "于是原台词是：",
            "“零钱？什么？没有。”",
            "后来变成：",
            "“零钱？……没有。”",
            "下一段。",
        ]
    )

    assert [item.text for item in paragraphs] == [
        (
            "于是原台词是： “零钱？什么？没有。” "
            "后来变成： “零钱？……没有。”"
        ),
        "下一段。",
    ]


def test_alignment_splits_standalone_quoted_dialogue_turns():
    paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
        [
            "前一段。",
            "“你看到我的鞋了。”“啊！”“黑崎。”“原来如此，那家伙很强。”",
            "后一段。",
        ]
    )

    assert [item.text for item in paragraphs] == [
        "前一段。",
        "“你看到我的鞋了。”",
        "“啊！”",
        "“黑崎。”",
        "“原来如此，那家伙很强。”",
        "后一段。",
    ]
    assert [item.paragraph_id for item in paragraphs] == [
        f"paragraph_{index:04d}" for index in range(1, 7)
    ]


def test_alignment_splits_unquoted_short_dialogue_turns():
    paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
        [
            "我答应。你赢了！快停下！改成两个月。两个月？不行！",
            "后面的长篇讲解保持为另一个段落。",
        ]
    )

    assert [item.text for item in paragraphs] == [
        "我答应。",
        "你赢了！",
        "快停下！",
        "改成两个月。",
        "两个月？",
        "不行！",
        "后面的长篇讲解保持为另一个段落。",
    ]


def test_alignment_restores_blank_line_paragraphs_from_one_model_field():
    paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
        [
            "第一段。\n\n第二段。\n\n第三段。",
            "后一段。",
        ]
    )

    assert [item.text for item in paragraphs] == [
        "第一段。",
        "第二段。",
        "第三段。",
        "后一段。",
    ]


def test_extra_target_turn_does_not_shift_later_speaker_runs():
    request = _request(
        [
            ("paragraph_0001", "长回答前半部分"),
            ("paragraph_0002", "好。"),
            ("paragraph_0003", "长回答后半部分"),
            ("paragraph_0004", "请点击订阅按钮"),
            ("paragraph_0005", "比特币？"),
        ],
        [
            ("cue_0001", "long answer first"),
            ("cue_0002", "long answer second"),
            ("cue_0003", "please subscribe"),
            ("cue_0004", "click the button"),
            ("cue_0005", "bitcoin or"),
        ],
        source_speakers=[
            "speaker_02",
            "speaker_02",
            "speaker_01",
            "speaker_01",
            "speaker_02",
        ],
    )
    encoder = _FixtureEncoder(
        {
            "长回答前半部分": [1.0, 0.0, 0.0],
            "好。": [0.6, 0.6, 0.0],
            "长回答后半部分": [1.0, 0.0, 0.0],
            "请点击订阅按钮": [0.0, 1.0, 0.0],
            "比特币？": [0.0, 0.0, 1.0],
            "long answer first": [1.0, 0.0, 0.0],
            "long answer second": [1.0, 0.0, 0.0],
            "please subscribe": [0.0, 1.0, 0.0],
            "click the button": [0.0, 1.0, 0.0],
            "bitcoin or": [0.0, 0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )
    block_by_paragraph = {
        paragraph_id: block
        for block in result.blocks
        for paragraph_id in block.paragraph_ids
    }

    assert block_by_paragraph["paragraph_0004"].source_cue_ids == [
        "cue_0003",
        "cue_0004",
    ]
    assert block_by_paragraph["paragraph_0005"].source_cue_ids == [
        "cue_0005"
    ]


def test_semantic_alignment_owns_complete_monotonic_coverage():
    request = _request(
        [
            ("paragraph_0001", "中文甲"),
            ("paragraph_0002", "中文乙"),
        ],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "source a detail"),
            ("cue_0003", "source b"),
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0],
            "中文乙": [0.0, 1.0],
            "source a": [1.0, 0.0],
            "source a detail": [0.98, 0.02],
            "source b": [0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert [
        item
        for block in result.blocks
        for item in block.paragraph_ids
    ] == ["paragraph_0001", "paragraph_0002"]
    assert [
        item
        for block in result.blocks
        for item in block.source_cue_ids
    ] == ["cue_0001", "cue_0002", "cue_0003"]
    assert result.quality_summary.target_coverage_complete is True
    assert result.quality_summary.source_coverage_complete is True
    assert result.quality_summary.source_order_preserved is True
    assert result.quality_summary.model_call_count == 0

    projection = (
        localization_semantic_alignment
        .project_localization_semantic_alignment_result(
            result,
            source_cues=request.source_cues,
            target_paragraphs=request.target_paragraphs,
        )
    )
    assert projection["coverage"] == {
        "mode": "complete",
        "shown_count": 2,
        "total_count": 2,
        "unit": "组语义映射",
    }
    assert projection["sections"] == [
        {
            "title": "全部语义映射",
            "open_by_default": False,
            "items": [
                {
                    "title": "语义段 0001",
                    "before_label": "本土化内容",
                    "before": "中文甲",
                    "after_label": "对应英文",
                    "after": "source a source a detail",
                    "text": "",
                    "meta": "00:00.000–00:02.000 · 高把握",
                    "facts": [],
                    "links": [],
                    "tone": "positive",
                },
                {
                    "title": "语义段 0002",
                    "before_label": "本土化内容",
                    "before": "中文乙",
                    "after_label": "对应英文",
                    "after": "source b",
                    "text": "",
                    "meta": "00:02.000–00:03.000 · 高把握",
                    "facts": [],
                    "links": [],
                    "tone": "positive",
                },
            ],
        }
    ]


    assert projection["debug"]["metrics"] == [
        {
            "label": "本地向量模型",
            "value": "sentence-transformers/LaBSE",
        },
        {
            "label": "对齐器版本",
            "value": "localization-semantic-alignment-v14",
        },
        {"label": "模型调用", "value": "0"},
        {
            "label": "中英文完整覆盖",
            "value": "通过",
        },
        {"label": "英文顺序保持", "value": "通过"},
        {"label": "时间窗无重叠", "value": "通过"},
    ]
    assert projection["debug"]["sections"] == [
        {
            "title": "语义映射内部依据",
            "items": [
                {
                    "title": "语义段 0001",
                    "meta": "00:00.000–00:02.000",
                    "facts": [
                        {
                            "label": "英文字幕编号",
                            "value": "cue_0001–cue_0002",
                        },
                        {"label": "向量相似度", "value": "1.000"},
                    ],
                    "links": [],
                    "tone": "neutral",
                },
                {
                    "title": "语义段 0002",
                    "meta": "00:02.000–00:03.000",
                    "facts": [
                        {
                            "label": "英文字幕编号",
                            "value": "cue_0003",
                        },
                        {"label": "向量相似度", "value": "1.000"},
                    ],
                    "links": [],
                    "tone": "neutral",
                },
            ],
        }
    ]


def test_verified_evidence_anchor_keeps_unrecognized_source_cue_with_target():
    request = _request(
        [
            ("paragraph_0001", "我的天！真的是你吗？"),
            ("paragraph_0002", "拿上板子，我们得走了。"),
        ],
        [
            ("cue_0001", "Oh my god!"),
            ("cue_0002", "unrecognized creature speech"),
            ("cue_0003", "Grab your boards, we need to move."),
        ],
    ).model_copy(
        update={
            "evidence_anchors": [
                localization_semantic_alignment
                .LocalizationSemanticAlignmentAnchor(
                    anchor_id="question_0001",
                    target_paragraph_id="paragraph_0001",
                    source_cue_ids=["cue_0002"],
                )
            ]
        }
    )
    encoder = _FixtureEncoder(
        {
            "我的天！真的是你吗？": [1.0, 0.0],
            "拿上板子，我们得走了。": [0.0, 1.0],
            "Oh my god!": [1.0, 0.0],
            "unrecognized creature speech": [0.0, 1.0],
            "Grab your boards, we need to move.": [0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert result.blocks[0].paragraph_ids == ["paragraph_0001"]
    assert result.blocks[0].source_cue_ids == ["cue_0001", "cue_0002"]
    assert result.blocks[0].evidence_anchor_ids == ["question_0001"]
    assert result.blocks[1].source_cue_ids == ["cue_0003"]


def test_semantic_alignment_consolidates_localized_bridge_for_review():
    request = _request(
        [
            ("paragraph_0001", "中文甲"),
            ("paragraph_0002", "承上启下"),
            ("paragraph_0003", "中文乙"),
        ],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "source b"),
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0, 0.0],
            "承上启下": [0.0, 0.0, 1.0],
            "中文乙": [0.0, 1.0, 0.0],
            "source a": [1.0, 0.0, 0.0],
            "source b": [0.0, 1.0, 0.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert [
        item
        for block in result.blocks
        for item in block.paragraph_ids
    ] == [
        "paragraph_0001",
        "paragraph_0002",
        "paragraph_0003",
    ]
    assert result.quality_summary.status == "warning"
    assert result.quality_summary.adjudication_block_count >= 1
    assert any(
        "unmatched_content_consolidated" in block.reason_codes
        for block in result.blocks
    )


def test_unmatched_target_attaches_to_semantically_closer_next_block():
    transitions = [
        localization_semantic_alignment._Transition(
            previous_target=0,
            previous_source=0,
            target_count=1,
            source_count=1,
            similarity=1.0,
            cost=0.0,
        ),
        localization_semantic_alignment._Transition(
            previous_target=1,
            previous_source=1,
            target_count=1,
            source_count=0,
            similarity=0.0,
            cost=0.0,
        ),
        localization_semantic_alignment._Transition(
            previous_target=2,
            previous_source=1,
            target_count=1,
            source_count=1,
            similarity=1.0,
            cost=0.0,
        ),
    ]

    groups = localization_semantic_alignment._consolidate_unmatched(
        transitions,
        target_vectors=np.asarray(
            [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]],
            dtype=np.float32,
        ),
        source_vectors=np.asarray(
            [[1.0, 0.0], [0.0, 1.0]],
            dtype=np.float32,
        ),
    )

    assert groups == [[transitions[0]], transitions[1:]]


def test_broad_but_high_similarity_source_window_does_not_call_for_llm_review():
    request = _request(
        [("paragraph_0001", "完整中文语义段")],
        [
            (f"cue_{index:04d}", f"source detail {index}")
            for index in range(1, 9)
        ],
    )
    encoder = _FixtureEncoder(
        {
            "完整中文语义段": [1.0, 0.0],
            **{
                f"source detail {index}": [1.0, 0.0]
                for index in range(1, 9)
            },
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert result.blocks[0].source_cue_ids == [
        f"cue_{index:04d}" for index in range(1, 9)
    ]
    assert "broad_source_window" in result.blocks[0].reason_codes
    assert result.blocks[0].needs_adjudication is False
    assert result.quality_summary.status == "passed"


def test_default_policy_keeps_long_source_span_with_its_semantic_paragraph():
    request = _request(
        [
            ("paragraph_0001", "中文长段落"),
            ("paragraph_0002", "中文乙"),
        ],
        [
            *[
                (f"cue_{index:04d}", f"source a detail {index}")
                for index in range(1, 71)
            ],
            ("cue_0071", "source b"),
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文长段落": [1.0, 0.0],
            "中文乙": [0.0, 1.0],
            **{
                f"source a detail {index}": [1.0, 0.0]
                for index in range(1, 71)
            },
            "source b": [0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert request.policy.maximum_source_group == 128
    assert result.blocks[0].paragraph_ids == ["paragraph_0001"]
    assert result.blocks[0].source_cue_ids == [
        f"cue_{index:04d}" for index in range(1, 71)
    ]
    assert result.blocks[1].paragraph_ids == ["paragraph_0002"]
    assert result.blocks[1].source_cue_ids == ["cue_0071"]
    assert result.blocks[1].confidence == "high"


def test_path_search_builds_each_source_group_vector_only_once(monkeypatch):
    targets = [
        localization_semantic_alignment.LocalizationAlignmentTargetParagraph(
            paragraph_id=f"paragraph_{index:04d}",
            text=f"中文内容 {index}",
        )
        for index in range(1, 4)
    ]
    sources = [
        localization_semantic_alignment.LocalizationAlignmentSourceCue(
            cue_id=f"cue_{index:04d}",
            text=f"source content {index}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            source_word_ids=[f"word_{index:06d}"],
        )
        for index in range(1, 5)
    ]
    target_vectors = np.asarray(
        [[1.0, 0.0], [0.8, 0.2], [0.0, 1.0]],
        dtype=np.float32,
    )
    source_vectors = np.asarray(
        [[1.0, 0.0], [0.9, 0.1], [0.2, 0.8], [0.0, 1.0]],
        dtype=np.float32,
    )
    original = localization_semantic_alignment._normalized_mean
    calls = 0

    def counted(vectors, start, count):
        nonlocal calls
        calls += 1
        return original(vectors, start, count)

    monkeypatch.setattr(
        localization_semantic_alignment,
        "_normalized_mean",
        counted,
    )

    localization_semantic_alignment._best_path(
        targets,
        sources,
        target_vectors,
        source_vectors,
        localization_semantic_alignment.LocalizationSemanticAlignmentPolicy(
            maximum_source_group=2,
        ),
    )

    assert calls == 7


def test_evidence_anchors_follow_document_order_not_question_order():
    paragraphs = [
        localization_semantic_alignment.LocalizationAlignmentTargetParagraph(
            paragraph_id="paragraph_0001",
            text="较早发生的怪物对白。",
        ),
        localization_semantic_alignment.LocalizationAlignmentTargetParagraph(
            paragraph_id="paragraph_0002",
            text="较晚发生的母亲名字对白。",
        ),
    ]
    hints = [
        localization_semantic_alignment.LocalizationSemanticAlignmentEvidenceHint(
            anchor_id="question_0016",
            target_text_zh="母亲名字",
            source_cue_ids=["cue_0791"],
        ),
        localization_semantic_alignment.LocalizationSemanticAlignmentEvidenceHint(
            anchor_id="question_0018",
            target_text_zh="怪物对白",
            source_cue_ids=["cue_0730"],
        ),
    ]

    anchors = localization_semantic_alignment.build_alignment_evidence_anchors(
        hints,
        paragraphs,
        [
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id="cue_0730",
                text="earlier source",
                start_ms=0,
                end_ms=1_000,
                source_word_ids=["word_0001"],
            ),
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id="cue_0791",
                text="later source",
                start_ms=1_000,
                end_ms=2_000,
                source_word_ids=["word_0002"],
            ),
        ],
    )

    assert [item.anchor_id for item in anchors] == [
        "question_0018",
        "question_0016",
    ]


def test_evidence_anchor_cues_follow_source_timeline_not_evidence_order():
    source_cues = [
        localization_semantic_alignment.LocalizationAlignmentSourceCue(
            cue_id=f"cue_{index:04d}",
            text=f"source {index}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            source_word_ids=[f"word_{index:04d}"],
        )
        for index in range(1, 7)
    ]

    anchors = localization_semantic_alignment.build_alignment_evidence_anchors(
        [
            localization_semantic_alignment
            .LocalizationSemanticAlignmentEvidenceHint(
                anchor_id="question_0012",
                target_text_zh="第三场镜头已经锁定",
                source_cue_ids=[
                    "cue_0002",
                    "cue_0003",
                    "cue_0004",
                    "cue_0005",
                    "cue_0001",
                    "cue_0006",
                ],
            )
        ],
        [
            localization_semantic_alignment
            .LocalizationAlignmentTargetParagraph(
                paragraph_id="paragraph_0001",
                text="好，第三场镜头已经锁定。",
            )
        ],
        source_cues,
    )

    assert anchors[0].source_cue_ids == [
        f"cue_{index:04d}" for index in range(1, 7)
    ]


def test_evidence_anchor_skips_context_that_crosses_speaker_turns():
    anchors = localization_semantic_alignment.build_alignment_evidence_anchors(
        [
            localization_semantic_alignment
            .LocalizationSemanticAlignmentEvidenceHint(
                anchor_id="question_0001",
                target_text_zh="下一步是什么？",
                source_cue_ids=["cue_0001", "cue_0002", "cue_0003"],
            )
        ],
        [
            localization_semantic_alignment
            .LocalizationAlignmentTargetParagraph(
                paragraph_id="paragraph_0001",
                text="好，下一步是什么？",
            )
        ],
        [
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id=f"cue_{index:04d}",
                speaker_id=speaker_id,
                text=f"source {index}",
                start_ms=(index - 1) * 1_000,
                end_ms=index * 1_000,
                source_word_ids=[f"word_{index:04d}"],
            )
            for index, speaker_id in enumerate(
                ["speaker_01", "speaker_02", "speaker_01"],
                start=1,
            )
        ],
    )

    assert anchors == []


def test_semantic_alignment_anchors_starts_and_clamps_previous_end():
    request = _request(
        [
            ("paragraph_0001", "中文甲"),
            ("paragraph_0002", "中文乙"),
        ],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "source b"),
        ],
    )
    request = request.model_copy(
        update={
            "source_cues": [
                request.source_cues[0].model_copy(
                    update={"start_ms": 100, "end_ms": 1_400}
                ),
                request.source_cues[1].model_copy(
                    update={"start_ms": 1_000, "end_ms": 2_000}
                ),
            ]
        }
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0],
            "中文乙": [0.0, 1.0],
            "source a": [1.0, 0.0],
            "source b": [0.0, 1.0],
        }
    )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )

    assert result.blocks[0].source_start_ms == 100
    assert result.blocks[0].source_end_ms == 1_000
    assert result.blocks[1].source_start_ms == 1_000
    assert result.blocks[0].source_end_ms <= result.blocks[1].source_start_ms
    assert (
        result.quality_summary.semantic_starts_source_anchored is True
    )
    assert (
        result.quality_summary.semantic_windows_non_overlapping is True
    )


def test_semantic_alignment_refines_one_cue_at_a_supported_word_boundary():
    source_words = [
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
                ("Each", 90_000, 90_300),
                ("one", 90_320, 90_520),
                ("bigger", 90_540, 90_900),
                ("and", 90_920, 91_080),
                ("harder", 91_100, 91_500),
                ("than", 91_520, 91_720),
                ("the", 91_740, 91_860),
                ("last.", 91_880, 92_200),
                ("Level", 92_840, 93_200),
                ("one", 93_220, 93_520),
                ("swap", 93_840, 94_200),
                ("world", 94_220, 94_700),
            ],
            start=1,
        )
    ]
    request = localization_semantic_alignment.LocalizationSemanticAlignmentInput(
        source_fingerprint="a" * 64,
        spoken_script_fingerprint="b" * 64,
        source_cues=[
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id="cue_0001",
                text="Each one bigger and harder than the last Level one",
                start_ms=90_000,
                end_ms=93_520,
                source_word_ids=[
                    item.word_id for item in source_words[:10]
                ],
            ),
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id="cue_0002",
                text="swap world",
                start_ms=93_840,
                end_ms=94_700,
                source_word_ids=[
                    item.word_id for item in source_words[10:]
                ],
            ),
        ],
        source_words=source_words,
        source_boundaries=[
            SourceBoundaryEvidence(
                boundary_id=(
                    f"{source_words[position - 1].word_id}:"
                    f"{source_words[position].word_id}"
                ),
                left_word_id=source_words[position - 1].word_id,
                right_word_id=source_words[position].word_id,
                left_cue_id=(
                    "cue_0001" if position <= 10 else "cue_0002"
                ),
                right_cue_id=(
                    "cue_0001" if position < 10 else "cue_0002"
                ),
                position_after_word=position,
                cue_boundary=position == 10,
                eligible=position in {8, 10},
                punctuation="terminal" if position == 8 else "none",
                pause_ms=640 if position == 8 else 320 if position == 10 else 20,
                pause_confidence="high" if position == 8 else "none",
                segment_change=False,
                speaker_change=False,
                overlap_speech=False,
                objective_support=position in {8, 10},
                classification=(
                    "preferred"
                    if position in {8, 10}
                    else "internal_cue"
                ),
                score=15 if position == 8 else 1 if position == 10 else 0,
                reason_codes=(
                    ["terminal_punctuation", "confirmed_pause"]
                    if position == 8
                    else ["cue_boundary"]
                    if position == 10
                    else []
                ),
            )
            for position in range(1, len(source_words))
        ],
        target_paragraphs=[
            localization_semantic_alignment.LocalizationAlignmentTargetParagraph(
                paragraph_id="paragraph_0001",
                text="全片分成三个层级，而且一个比一个难。",
            ),
            localization_semantic_alignment.LocalizationAlignmentTargetParagraph(
                paragraph_id="paragraph_0002",
                text="第一层，替换人物周围的世界。",
            ),
        ],
    )

    class _SemanticFixtureEncoder:
        model_id = localization_semantic_alignment.MODEL_ID
        model_fingerprint = "fixture-word-refinement-v1"

        def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for text in texts:
                if text.startswith("全片分成"):
                    rows.append([1.0, 0.0])
                elif text.startswith("第一层"):
                    rows.append([0.0, 1.0])
                elif "Level one" in text and "swap world" in text:
                    rows.append([0.0, 1.0])
                elif text.endswith("last."):
                    rows.append([1.0, 0.0])
                elif text == "swap world":
                    rows.append([0.0, 1.0])
                elif "bigger" in text and "Level one" in text:
                    rows.append([0.72, 0.69])
                else:
                    rows.append([0.5, 0.5])
            values = np.asarray(rows, dtype=np.float32)
            return values / np.maximum(
                np.linalg.norm(values, axis=1, keepdims=True),
                1e-12,
            )

    result = localization_semantic_alignment.align_localized_script(
        request,
        encoder=_SemanticFixtureEncoder(),
    )

    assert result.blocks[0].source_word_ids[-1] == "word_0008"
    assert result.blocks[0].source_end_ms == 92_200
    assert result.blocks[1].source_word_ids[:2] == [
        "word_0009",
        "word_0010",
    ]
    assert result.blocks[1].source_start_ms == 92_840
    assert result.blocks[0].source_cue_ids == ["cue_0001"]
    assert result.blocks[1].source_cue_ids == ["cue_0001", "cue_0002"]
    assert "word_boundary_refined" in result.blocks[0].reason_codes
    assert [
        word_id
        for block in result.blocks
        for word_id in block.source_word_ids
    ] == [item.word_id for item in source_words]


def test_semantic_alignment_sends_extreme_adjacent_density_to_review():
    request = _request(
        [
            ("paragraph_0001", "简短结论"),
            (
                "paragraph_0002",
                "这是一段明显更长但只得到很短时间的后续说明内容",
            ),
        ],
        [
            ("cue_0001", "long source passage"),
            ("cue_0002", "short tail"),
        ],
    )
    request = request.model_copy(
        update={
            "source_cues": [
                request.source_cues[0].model_copy(
                    update={"start_ms": 0, "end_ms": 10_000}
                ),
                request.source_cues[1].model_copy(
                    update={"start_ms": 10_000, "end_ms": 12_000}
                ),
            ]
        }
    )
    blocks = [
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0001",
            kind="semantic_match",
            paragraph_ids=["paragraph_0001"],
            source_cue_ids=["cue_0001"],
            source_word_ids=["word_0000"],
            source_start_ms=0,
            source_end_ms=10_000,
            similarity=0.4,
            confidence="medium",
        ),
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0002",
            kind="semantic_match",
            paragraph_ids=["paragraph_0002"],
            source_cue_ids=["cue_0002"],
            source_word_ids=["word_0001"],
            source_start_ms=10_000,
            source_end_ms=12_000,
            similarity=0.4,
            confidence="medium",
        ),
    ]

    flagged = (
        localization_semantic_alignment
        ._flag_extreme_adjacent_density_imbalances(request, blocks)
    )

    assert flagged[0].needs_adjudication is False
    assert flagged[1].needs_adjudication is True
    assert flagged[1].confidence == "low"
    assert flagged[1].kind == "ambiguous_semantic_group"
    assert "adjacent_target_density_imbalance" in (
        flagged[1].reason_codes
    )


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_boundary_routes_a_misowned_lead_phrase_to_review(confidence):
    request = _request(
        [
            ("paragraph_0001", "左侧内容。"),
            ("paragraph_0002", "过渡短语？右侧内容。"),
        ],
        [
            ("cue_0001", "left"),
            ("cue_0002", "bridge"),
            ("cue_0003", "right"),
        ],
        source_speakers=["speaker_01"] * 3,
    ).model_copy(
        update={
            "source_words": [
                LocalizationSourceWord(
                    word_id=f"word_{index:04d}",
                    segment_id=f"segment_{index:04d}",
                    text=text,
                    start_ms=(index - 1) * 1_000,
                    end_ms=index * 1_000,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
                for index, text in enumerate(
                    ["left", "bridge", "right"],
                    start=1,
                )
            ],
            "source_cues": [
                item.model_copy(
                    update={
                        "source_word_ids": [f"word_{index:04d}"]
                    }
                )
                for index, item in enumerate(
                    _request(
                        [("paragraph_0001", "左"), ("paragraph_0002", "右")],
                        [
                            ("cue_0001", "left"),
                            ("cue_0002", "bridge"),
                            ("cue_0003", "right"),
                        ],
                        source_speakers=["speaker_01"] * 3,
                    ).source_cues,
                    start=1,
                )
            ],
        }
    )
    blocks = [
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0001",
            kind="semantic_match",
            paragraph_ids=["paragraph_0001"],
            source_cue_ids=["cue_0001", "cue_0002"],
            source_word_ids=["word_0001", "word_0002"],
            source_start_ms=0,
            source_end_ms=2_000,
            similarity=0.4,
            confidence="medium",
        ),
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0002",
            kind="semantic_match",
            paragraph_ids=["paragraph_0002"],
            source_cue_ids=["cue_0003"],
            source_word_ids=["word_0003"],
            source_start_ms=2_000,
            source_end_ms=3_000,
            similarity=0.4,
            confidence="medium",
        ),
    ]
    encoder = _FixtureEncoder(
        {
            "左侧内容。": [1.0, 0.0],
            "过渡短语？": [0.0, 1.0],
            "left": [1.0, 0.0],
            "bridge right": [0.0, 1.0],
            "left bridge": [1.0, 1.0],
            "right": [1.0, 0.0],
        }
    )

    blocks = [block.model_copy(update={"confidence": confidence}) for block in blocks]
    flagged = localization_semantic_alignment._flag_boundary_edge_semantic_ambiguities(
        request,
        blocks,
        encoder=encoder,
    )

    assert flagged[0].needs_adjudication is False
    assert flagged[1].needs_adjudication is True
    assert flagged[1].confidence == "low"
    assert "boundary_edge_semantic_ambiguity" in flagged[1].reason_codes


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_boundary_edge_check_keeps_a_correct_short_lead_with_its_right_side(confidence):
    request = _request(
        [
            ("paragraph_0001", "左侧内容。"),
            ("paragraph_0002", "过渡短语？右侧内容。"),
        ],
        [
            ("cue_0001", "left"),
            ("cue_0002", "bridge"),
            ("cue_0003", "right"),
        ],
        source_speakers=["speaker_01"] * 3,
    ).model_copy(
        update={
            "source_words": [
                LocalizationSourceWord(
                    word_id=f"word_{index:04d}",
                    segment_id=f"segment_{index:04d}",
                    text=text,
                    start_ms=(index - 1) * 1_000,
                    end_ms=index * 1_000,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
                for index, text in enumerate(
                    ["left", "bridge", "right"],
                    start=1,
                )
            ],
            "source_cues": [
                item.model_copy(
                    update={
                        "source_word_ids": [f"word_{index:04d}"]
                    }
                )
                for index, item in enumerate(
                    _request(
                        [("paragraph_0001", "左"), ("paragraph_0002", "右")],
                        [
                            ("cue_0001", "left"),
                            ("cue_0002", "bridge"),
                            ("cue_0003", "right"),
                        ],
                        source_speakers=["speaker_01"] * 3,
                    ).source_cues,
                    start=1,
                )
            ],
        }
    )
    blocks = [
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0001",
            kind="semantic_match",
            paragraph_ids=["paragraph_0001"],
            source_cue_ids=["cue_0001"],
            source_word_ids=["word_0001"],
            source_start_ms=0,
            source_end_ms=1_000,
            similarity=0.4,
            confidence="medium",
        ),
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0002",
            kind="semantic_match",
            paragraph_ids=["paragraph_0002"],
            source_cue_ids=["cue_0002", "cue_0003"],
            source_word_ids=["word_0002", "word_0003"],
            source_start_ms=1_000,
            source_end_ms=3_000,
            similarity=0.4,
            confidence="medium",
        ),
    ]
    encoder = _FixtureEncoder(
        {
            "左侧内容。": [1.0, 0.0],
            "过渡短语？": [0.0, 1.0],
            "left": [1.0, 0.0],
            "bridge right": [0.0, 1.0],
            "left bridge": [1.0, 1.0],
            "right": [1.0, 0.0],
        }
    )

    flagged = localization_semantic_alignment._flag_boundary_edge_semantic_ambiguities(
        request,
        blocks,
        encoder=encoder,
    )

    assert not any(item.needs_adjudication for item in flagged)
    blocks_for_confidence = [
        block.model_copy(update={"confidence": confidence}) for block in blocks
    ]
    checked = localization_semantic_alignment._flag_boundary_edge_semantic_ambiguities(
        request, blocks_for_confidence, encoder=encoder,
    )
    assert not any(item.needs_adjudication for item in checked)


def test_semantic_alignment_does_not_let_high_similarity_hide_extreme_timing_imbalance():
    request = _request(
        [
            ("paragraph_0001", "Seedance！"),
            ("paragraph_0002", "这一批我会取鸵鸟的反应"),
        ],
        [
            ("cue_0001", "Seedance! So from this batch I'm taking"),
            ("cue_0002", "the ostrich reaction"),
        ],
    )
    blocks = [
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0001",
            kind="semantic_match",
            paragraph_ids=["paragraph_0001"],
            source_cue_ids=["cue_0001"],
            source_word_ids=["word_0000"],
            source_start_ms=0,
            source_end_ms=15_600,
            similarity=0.96,
            confidence="high",
        ),
        localization_semantic_alignment.LocalizationSemanticAlignmentBlock(
            block_id="alignment_block_0002",
            kind="semantic_match",
            paragraph_ids=["paragraph_0002"],
            source_cue_ids=["cue_0002"],
            source_word_ids=["word_0001"],
            source_start_ms=15_600,
            source_end_ms=18_800,
            similarity=0.94,
            confidence="high",
        ),
    ]

    flagged = (
        localization_semantic_alignment
        ._flag_extreme_adjacent_density_imbalances(request, blocks)
    )

    assert any(item.needs_adjudication for item in flagged)
    assert any(
        "adjacent_target_density_imbalance" in item.reason_codes
        for item in flagged
    )
