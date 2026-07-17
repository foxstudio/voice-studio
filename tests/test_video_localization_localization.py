import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.errors import AppException  # noqa: E402
from app.services.llm_runtime import LlmRuntimeError  # noqa: E402


def _draft() -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        scene_context="一位创作者介绍视频特效工作流",
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                speaker_id="speaker_01",
                start_ms=1000,
                end_ms=2500,
                en_subtitle_text="In 1992, this changed",
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                speaker_id="speaker_01",
                start_ms=2600,
                end_ms=4200,
                en_subtitle_text="everything for creators",
            ),
        ],
    )


def _timed_item(index: int, *, gap_ms: int = 100) -> dict:
    start_ms = index * 1000 + gap_ms
    return {
        "id": f"localized_{index + 1:04d}",
        "start_ms": start_ms,
        "end_ms": start_ms + 800,
        "source_text": f"Source phrase {chr(97 + index % 26)}",
        "display_text": f"第{chr(0x4E00 + index % 20)}句",
        "tts_text": f"第{chr(0x4E00 + index % 20)}句。",
        "quality_flags": [],
        "cps": 4.0,
    }


def test_localization_batches_keep_a_medium_transcript_in_one_request():
    cues = [
        VideoLocalizationCue(
            cue_id=f"cue_{index:04d}",
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
            en_subtitle_text="one short source sentence",
            source_word_ids=[f"word_{index:04d}_{word}" for word in range(5)],
        )
        for index in range(70)
    ]

    batches = localization._localization_batches(cues)

    assert [(start, len(batch)) for start, batch in batches] == [(0, 70)]
    assert all(
        sum(len(cue.source_word_ids) for cue in batch) <= localization.LOCALIZATION_BATCH_MAX_WORDS
        for _, batch in batches
    )


def test_source_word_partition_avoids_splitting_negation_before_sentence_end():
    tokens = ["that", "definitely", "should", "not", "be", "there.", "Insane,", "right?"]
    words = {
        f"word_{index:04d}": VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_1",
            text=token,
            start_ms=index * 200,
            end_ms=index * 200 + 160,
        )
        for index, token in enumerate(tokens, start=1)
    }
    word_ids = list(words)

    partitions = localization._partition_ids_by_text_weight(
        word_ids,
        ["比如在身后放个不该出现的东西", "很夸张吧？"],
        word_by_id=words,
    )

    assert [words[word_id].text for word_id in partitions[0]][-3:] == ["not", "be", "there."]
    assert [words[word_id].text for word_id in partitions[1]] == ["Insane,", "right?"]


def test_localization_batches_reject_a_single_unbounded_source_cue():
    cue = VideoLocalizationCue(
        cue_id="cue_too_large",
        start_ms=0,
        end_ms=1000,
        en_subtitle_text="source",
        source_word_ids=[f"word_{index:04d}" for index in range(localization.LOCALIZATION_BATCH_MAX_WORDS + 1)],
    )

    with pytest.raises(AppException) as exc_info:
        localization._localization_batches([cue])

    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_CUE_TOO_LARGE"


def test_context_analysis_retries_one_invalid_structured_response(monkeypatch):
    prompts: list[str] = []

    def complete_json(**kwargs):
        prompts.append(kwargs["system_prompt"])
        if len(prompts) == 1:
            raise LlmRuntimeError("invalid json", code="llm_json_invalid", status_code=502)
        return {
            "content_type": "tutorial",
            "audience": "刚开始学习视频制作的观众",
            "register": "清楚、亲切的教程口语",
            "overview": "创作者介绍工作流",
            "era": "当代",
            "setting": "教程",
            "topics": ["视频创作"],
            "speakers": [],
            "style_rules": ["自然口语"],
            "needs_research": False,
            "research_questions": [],
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    result = localization._analyze_context(
        _draft(),
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        localization_level="L1",
        worldview_permeability="W0",
    )

    assert len(prompts) == 2
    assert "上次响应无法解析" in prompts[1]
    assert result["overview"] == "创作者介绍工作流"
    assert result["content_type"] == "technology_tutorial"
    assert result["audience"] == "刚开始学习视频制作的观众"
    assert result["register"] == "清楚、亲切的教程口语"


def test_context_analysis_samples_long_transcript_across_the_full_document(monkeypatch):
    cues = [
        VideoLocalizationCue(
            cue_id=f"cue_{index:04d}",
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
            en_subtitle_text=f"section {index} has useful context",
        )
        for index in range(12)
    ]
    monkeypatch.setattr(localization, "CONTEXT_ANALYSIS_MAX_SOURCE_CHARS", 120)

    sampled, diagnostics = localization._context_transcript_sample(VideoLocalizationDraft(cues=cues))

    sampled_ids = {item["cue_id"] for item in sampled}
    assert "cue_0000" in sampled_ids
    assert "cue_0004" in sampled_ids
    assert "cue_0008" in sampled_ids
    assert diagnostics["mode"] == "distributed"
    assert diagnostics["included_chars"] <= localization.CONTEXT_ANALYSIS_MAX_SOURCE_CHARS
    assert diagnostics["included_cue_count"] < diagnostics["source_cue_count"]


def test_semantic_bundles_keep_text_cues_without_aligned_words():
    word = VideoLocalizationAlignedWord(
        word_id="word_0001",
        segment_id="segment_0001",
        text="first",
        start_ms=0,
        end_ms=400,
    )
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                speaker_id="speaker_01",
                start_ms=0,
                end_ms=500,
                en_subtitle_text="First line.",
                source_word_ids=[word.word_id],
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                speaker_id="speaker_01",
                start_ms=600,
                end_ms=1200,
                en_subtitle_text="Second line without aligned words.",
            ),
        ],
        transcription=VideoLocalizationTranscriptionState(
            words=[word],
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="segment_0001",
                    start_ms=0,
                    end_ms=500,
                    raw_text="First line.",
                ),
                VideoLocalizationTranscriptSegment(
                    segment_id="segment_0002",
                    start_ms=600,
                    end_ms=1200,
                    raw_text="Second line without aligned words.",
                ),
            ],
        ),
    )

    bundles = localization._semantic_localization_bundles(draft)

    assert [cue_id for bundle in bundles for cue_id in bundle["source_cue_ids"]] == ["cue_0001", "cue_0002"]
    assert "Second line without aligned words." in " ".join(bundle["source"] for bundle in bundles)


def test_semantic_bundles_never_join_different_speakers_mid_sentence():
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                speaker_id="speaker_01",
                start_ms=0,
                end_ms=500,
                en_subtitle_text="I think",
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                speaker_id="speaker_02",
                start_ms=600,
                end_ms=1200,
                en_subtitle_text="No absolutely not.",
            ),
        ]
    )

    bundles = localization._semantic_localization_bundles(draft)

    assert [bundle["speaker_id"] for bundle in bundles] == ["speaker_01", "speaker_02"]
    assert [bundle["source_cue_ids"] for bundle in bundles] == [["cue_0001"], ["cue_0002"]]


def test_full_document_localization_retries_one_damaged_json_response(monkeypatch):
    bundles = [
        {
            "id": "bundle_001",
            "speaker_id": "speaker_01",
            "source": "This is real footage.",
        }
    ]
    prompts = []
    payloads = []

    def complete_json(**kwargs):
        prompts.append(kwargs["system_prompt"])
        payloads.append(kwargs["user_payload"])
        if len(prompts) == 1:
            raise LlmRuntimeError("invalid json", code="llm_json_invalid", status_code=502)
        return {"items": [{"id": "bundle_001", "text": "这是真实拍摄的画面"}]}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    localized, diagnostics = localization._localize_semantic_bundles(
        bundles,
        draft=_draft(),
        context={},
        research={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        localization_level="L1",
        worldview_permeability="W0",
        is_cancelled=None,
    )

    assert localized[0]["text"] == "这是真实拍摄的画面"
    assert diagnostics["request_count"] == 2
    assert len(prompts) == 2
    assert "上次结构化响应损坏" in prompts[1]
    assert any("人物的 walk/gait" in rule for rule in payloads[0]["editorial_rules"])


@pytest.mark.parametrize(
    ("content_type", "audience", "register", "role_marker", "rule_marker"),
    [
        ("technology_tutorial", "软件入门观众", "清楚亲切的教程口语", "科技与教程", "工具、模型"),
        ("interview", "关注人物故事的观众", "自然克制的对谈口语", "访谈与对谈", "问答轮次"),
        ("news", "大众新闻观众", "中性准确的新闻表达", "新闻内容", "消息来源"),
        ("documentary_history", "历史纪录片观众", "沉稳严谨的旁白", "纪录片与历史", "史料来源"),
        ("drama_dialogue", "剧情片观众", "贴近角色的生活化对白", "剧情与角色对白", "潜台词"),
    ],
)
def test_localization_prompts_follow_content_profile_without_leaking_technology_rules(
    monkeypatch,
    content_type,
    audience,
    register,
    role_marker,
    rule_marker,
):
    calls: list[dict] = []

    def complete_json(**kwargs):
        calls.append(kwargs)
        if kwargs["user_payload"]["task"].endswith(":localize"):
            return {"items": [{"id": "bundle_001", "text": "示例中文"}]}
        return {"checked_count": 1, "changes": []}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    context = {
        "content_type": content_type,
        "audience": audience,
        "register": register,
    }
    bundles = [{"id": "bundle_001", "speaker_id": "speaker_01", "source": "Example source."}]

    localized, _diagnostics = localization._localize_semantic_bundles(
        bundles,
        draft=_draft(),
        context=context,
        research={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        localization_level="L1",
        worldview_permeability="W0",
        is_cancelled=None,
    )
    localization._review_localized_bundles(
        localized,
        context=context,
        profile_id="llm_default",
        is_cancelled=None,
    )

    generation_call, review_call = calls
    assert generation_call["user_payload"]["profile"] == {
        "localization_level": "L1",
        "worldview_permeability": "W0",
        "content_type": content_type,
        "audience": audience,
        "register": register,
    }
    assert review_call["user_payload"]["content_profile"] == {
        "content_type": content_type,
        "audience": audience,
        "register": register,
    }
    prompt_text = json.dumps(calls, ensure_ascii=False, default=str)
    assert role_marker in generation_call["system_prompt"]
    assert role_marker in review_call["system_prompt"]
    assert generation_call["disable_reasoning"] is True
    assert review_call["disable_reasoning"] is True
    assert rule_marker in prompt_text
    if content_type == "technology_tutorial":
        assert "4K" in prompt_text
    else:
        assert "4K" not in prompt_text
        assert "科技视频" not in prompt_text
        assert "短视频和 AI 创作工具" not in prompt_text


def test_oversized_localization_document_uses_continuous_anchored_chapters(monkeypatch):
    bundles = [
        {"id": f"bundle_{index:03d}", "speaker_id": "speaker_01", "source": character * 10}
        for index, character in enumerate(("A", "B", "C", "D"), start=1)
    ]
    payloads: list[dict] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        payloads.append(payload)
        return {"items": [{"id": item["id"], "text": f"中文 {item['id']}"} for item in payload["document"]]}

    monkeypatch.setattr(localization, "LOCALIZATION_DOCUMENT_MAX_SOURCE_CHARS", 20)
    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    localized, diagnostics = localization._localize_semantic_bundles(
        bundles,
        draft=_draft(),
        context={"overview": "完整视频"},
        research={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        localization_level="L1",
        worldview_permeability="W0",
        is_cancelled=None,
    )

    assert [item["id"] for item in localized] == [item["id"] for item in bundles]
    assert [[item["id"] for item in payload["document"]] for payload in payloads] == [
        ["bundle_001", "bundle_002"],
        ["bundle_003", "bundle_004"],
    ]
    assert payloads[0]["document_scope"]["next_anchor"]["id"] == "bundle_003"
    assert payloads[1]["document_scope"]["previous_anchor"]["id"] == "bundle_002"
    assert diagnostics["partition_count"] == 2
    assert diagnostics["request_count"] == 2
    assert diagnostics["source_chars"] == 40


def test_full_document_localization_retries_one_structurally_empty_item(monkeypatch):
    bundles = [
        {
            "id": "bundle_001",
            "speaker_id": "speaker_01",
            "source": "This is real footage.",
        }
    ]
    responses = iter(
        [
            {"items": [{"id": "bundle_001", "text": ""}]},
            {"items": [{"id": "bundle_001", "text": "这是真实拍摄的画面"}]},
        ]
    )
    monkeypatch.setattr(localization.llm_runtime, "complete_json", lambda **_kwargs: next(responses))

    localized, diagnostics = localization._localize_semantic_bundles(
        bundles,
        draft=_draft(),
        context={},
        research={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        localization_level="L1",
        worldview_permeability="W0",
        is_cancelled=None,
    )

    assert localized[0]["text"] == "这是真实拍摄的画面"
    assert diagnostics["request_count"] == 2


def test_pause_split_candidates_require_both_audio_gap_and_longer_chinese():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index}",
            segment_id="segment_01",
            text=f"w{index}",
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for index, (start_ms, end_ms) in enumerate(
            [(0, 160), (180, 340), (700, 860), (880, 1040)],
            start=1,
        )
    ]
    cue = VideoLocalizationCue(
        cue_id="cue_0001",
        start_ms=0,
        end_ms=1040,
        en_subtitle_text="one two three four",
        source_word_ids=[word.word_id for word in words],
    )
    word_by_id = {word.word_id: word for word in words}
    candidate = {
        "display_text": "这是一段已经偏长但仍没有触发硬上限的中文字幕",
        "source_word_ids": cue.source_word_ids,
    }

    assert localization._candidate_has_preferred_pause_split(candidate, word_by_id)
    assert not localization._candidate_has_preferred_pause_split(
        {**candidate, "display_text": "短句"},
        word_by_id,
    )
    assert localization._pause_boundary_payload([cue], word_by_id) == [
        {
            "after_word_id": "word_2",
            "before_word_id": "word_3",
            "left_cue_id": "cue_0001",
            "right_cue_id": "cue_0001",
            "gap_ms": 360,
            "strength": "clear",
            "instruction": "仅当边界两侧都能形成自然中文语义单位时优先在此拆分",
        }
    ]


def test_localization_review_focus_flags_calques_collocations_and_real_referents():
    context = {
        "content_type": "technology_tutorial",
        "speakers": [{"speaker_id": "speaker_01"}],
    }
    visual = localization._localization_review_focus(
        {
            "source_text": "This is the cleanest 4K AI and a small screen does not do it justice",
            "display_text": "这是最干净的 4K AI 画面 小屏幕体现不了它的好",
        },
        context,
    )
    tool_pronoun = localization._localization_review_focus(
        {
            "source_text": "This is the prompt he gave me",
            "display_text": "这是他给我的提示词",
        },
        context,
    )

    assert any("不能脱离对象机械使用‘干净’" in hint for hint in visual)
    assert any("体现不了" in hint for hint in visual)
    assert any("工具或 AI" in hint for hint in tool_pronoun)


def test_bundle_review_allows_a_focused_item_to_need_no_change(monkeypatch):
    bundles = [
        {
            "id": "bundle_0001",
            "source": "The image looks clean.",
            "text": "画面看起来很干净",
        }
    ]
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"checked_count": 1, "changes": []},
    )

    reviewed, changes, diagnostics = localization._review_localized_bundles(
        bundles,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == bundles
    assert changes == []
    assert diagnostics["request_count"] == 1


def test_bundle_review_retries_one_truncated_structured_response(monkeypatch):
    calls = []

    def complete_json(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise LlmRuntimeError("truncated", code="llm_output_truncated", status_code=502)
        return {
            "checked_count": 1,
            "changes": [
                {
                    "id": "bundle_0001",
                    "replacement": "这是同一个画面 用 Acme 3.1 做成的 4K 版本",
                    "reason": "修正模型与画质关系",
                }
            ],
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_localized_bundles(
        [
            {
                "id": "bundle_0001",
                "source": "This is the same shot mixed with Acme 3.1 in 4K.",
                "text": "这是同一个画面混了Acme 3.1的4K画质",
            }
        ],
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed[0]["text"] == "这是同一个画面 用 Acme 3.1 做成的 4K 版本"
    assert changes[0]["reason"] == "修正模型与画质关系"
    assert diagnostics["request_count"] == 2
    assert "上次结构化输出过长或损坏" in calls[1]["system_prompt"]


@pytest.mark.parametrize(
    ("source", "chinese", "expected"),
    [
        ("My face, my walk and the light", "我的脸、我的走路和光线", "不能按英语所有格直译"),
        ("Preserve the exact handheld camera move", "保留精确的手持运动", "手持运镜"),
        ("The rig framing the whole driving motion", "摄影机架设角度和整个开车的运动", "行车动作"),
        ("The rig keeps the framing stable", "保持摄影机架设角度稳定", "构图"),
        (
            "The rig framing the whole driving motion",
            "拍摄装置的整体行车动作取景",
            "并列保护项",
        ),
        (
            "The handheld move I shot on the ground is now a few thousand feet up",
            "我拍摄的手持运镜现在变成了几千英尺高",
            "运镜本身不会变成",
        ),
        ("A location swap with a moving camera", "用移动相机做场景替换", "不能写成像在搬设备"),
        ("That was a slow and steady camera movement", "刚才是个缓慢平稳的镜头", "不能只写成"),
    ],
)
def test_bundle_review_focus_distinguishes_action_motion_and_camera_language(source, chinese, expected):
    focus = localization._localized_bundle_review_focus(source, chinese)

    assert any(expected in hint for hint in focus)


def test_bundle_review_focus_does_not_turn_every_motion_into_a_trajectory():
    focus = localization._localized_bundle_review_focus(
        "The ball follows a visible curved trajectory",
        "球沿着清晰的弧形轨迹移动",
    )

    assert focus == []


def test_bundle_review_repairs_a_replacement_that_keeps_a_motion_relation_error(monkeypatch):
    calls = []
    responses = iter(
        [
            {
                "checked_count": 1,
                "changes": [
                    {
                        "id": "bundle_0001",
                        "replacement": "我在地面拍摄的手持运镜变成了几千英尺高",
                        "reason": "改用运镜术语",
                    }
                ],
            },
            {
                "items": [
                    {
                        "id": "bundle_0001",
                        "replacement": "原本在地面拍下的手持运镜 现在被搬到了几千英尺的高空",
                        "reason": "修正动作与空间关系",
                    }
                ]
            },
        ]
    )
    def complete_json(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_localized_bundles(
        [
            {
                "id": "bundle_0001",
                "source": "The handheld move I shot on the ground is now a few thousand feet up",
                "text": "我站在地上拍的手持运动现在变成了几千英尺高",
            }
        ],
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed[0]["text"] == "原本在地面拍下的手持运镜 现在被搬到了几千英尺的高空"
    assert changes[0]["reason"] == "修正动作与空间关系"
    assert diagnostics["request_count"] == 2
    assert calls[1]["disable_reasoning"] is True


def test_bundle_review_still_requires_a_missing_number_to_be_repaired(monkeypatch):
    bundles = [
        {
            "id": "bundle_0001",
            "source": "Version 2.0 renders in 4K.",
            "text": "这个版本可以渲染画面",
        }
    ]
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"checked_count": 1, "changes": []},
    )

    with pytest.raises(AppException) as exc_info:
        localization._review_localized_bundles(
            bundles,
            profile_id="llm_default",
            is_cancelled=None,
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_REVIEW_INVALID"


def test_reading_speed_counts_a_contiguous_product_name_as_one_unit():
    mixed_language = localization._candidate_budget_report(
        {
            "start_ms": 253_070,
            "end_ms": 254_410,
            "display_text": "在Seedance用此提示",
        }
    )
    chinese_only = localization._candidate_budget_report(
        {
            "start_ms": 253_070,
            "end_ms": 254_410,
            "display_text": "一二三四五六七八九十甲乙丙",
        }
    )

    assert mixed_language["visible_chars"] == 6
    assert mixed_language["reading_units"] == 6
    assert mixed_language["cps"] == 4.48
    assert mixed_language["violations"] == []
    assert chinese_only["visible_chars"] == 13
    assert chinese_only["reading_units"] == 13
    assert chinese_only["cps"] == 9.7
    assert chinese_only["violations"] == ["阅读速度超过每秒9.5字，需要在不丢信息的前提下精简表达"]

    short_boundary = localization._candidate_budget_report(
        {
            "start_ms": 0,
            "end_ms": 880,
            "display_text": "一二三四五六七八",
        }
    )
    assert short_boundary["cps"] == 9.09
    assert short_boundary["violations"] == []


def test_monotonic_alignment_uses_shared_product_and_number_anchors():
    source_units = [
        {"source": "First we prepare several ordinary clips", "word_count": 6},
        {"source": "Then Seedance 2.0 generates the result", "word_count": 6},
    ]
    target_chunks = ["先把普通素材准备好", "接着交给 Seedance 2.0 直接生成"]

    groups = localization._monotonic_length_alignment(source_units, target_chunks)

    anchored_group = next(group for group in groups if "Seedance" in " ".join(group[1]))
    assert any("Seedance 2.0" in unit["source"] for unit in anchored_group[0])


def test_quality_review_batches_obey_item_and_text_limits(monkeypatch):
    monkeypatch.setattr(localization, "QUALITY_REVIEW_BATCH_MAX_ITEMS", 3)
    monkeypatch.setattr(localization, "QUALITY_REVIEW_BATCH_MAX_TEXT_CHARS", 24)
    items = [
        {"id": f"localized_{index:04d}", "source_text": "source", "display_text": "字幕", "tts_text": "配音"}
        for index in range(5)
    ]

    batches = localization._quality_review_batches(items)

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert [item["id"] for batch in batches for item in batch] == [item["id"] for item in items]


def test_quality_review_splits_a_batch_after_invalid_json(monkeypatch):
    items = [
        {
            "id": f"localized_{index:04d}",
            "source_text": f"Source {index}",
            "display_text": f"字幕 {index}",
            "tts_text": f"字幕 {index}。",
            "start_ms": index * 1000,
            "end_ms": index * 1000 + 900,
        }
        for index in range(1, 5)
    ]
    calls: list[list[str]] = []
    review_rules: list[dict] = []

    def complete_json(**kwargs):
        ids = [item["id"] for item in kwargs["user_payload"]["items"]]
        calls.append(ids)
        review_rules.append(kwargs["user_payload"]["review_rules"])
        if len(ids) == 4:
            raise LlmRuntimeError("invalid json", code="llm_json_invalid", status_code=502)
        return {"checked_ids": ids, "changes": []}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    reviewed, changes = localization._quality_review(
        items,
        draft=_draft(),
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
        on_batch=lambda _completed, _total: None,
    )

    assert calls == [
        ["localized_0001", "localized_0002", "localized_0003", "localized_0004"],
        ["localized_0001", "localized_0002"],
        ["localized_0003", "localized_0004"],
    ]
    assert reviewed == items
    assert changes == []
    assert all("speaker_voice" in rules for rules in review_rules)
    assert all("discourse_markers" in rules for rules in review_rules)
    assert all("cultural_function" in rules for rules in review_rules)


def test_quality_review_reserves_recovery_requests_for_each_planned_batch(monkeypatch):
    items = [
        {
            "id": f"localized_{index:04d}",
            "source_text": f"Source {index}",
            "display_text": f"字幕 {index}",
            "tts_text": f"字幕 {index}。",
            "start_ms": index * 1000,
            "end_ms": index * 1000 + 900,
        }
        for index in range(1, 5)
    ]
    calls: list[list[str]] = []

    def complete_json(**kwargs):
        ids = [item["id"] for item in kwargs["user_payload"]["items"]]
        calls.append(ids)
        if len(ids) > 1:
            raise LlmRuntimeError("invalid json", code="llm_json_invalid", status_code=502)
        return {"checked_ids": ids, "changes": []}

    monkeypatch.setattr(localization, "QUALITY_REVIEW_BATCH_MAX_ITEMS", 2)
    monkeypatch.setattr(localization, "QUALITY_REVIEW_BATCH_MAX_TEXT_CHARS", 10_000)
    monkeypatch.setattr(localization, "QUALITY_REVIEW_MAX_REQUESTS", 2)
    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    diagnostics: dict = {}

    reviewed, changes = localization._quality_review(
        items,
        draft=_draft(),
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
        on_batch=lambda _completed, _total: None,
        diagnostics=diagnostics,
    )

    assert reviewed == items
    assert changes == []
    assert [len(ids) for ids in calls] == [2, 1, 1, 2, 1, 1]
    assert diagnostics["planned_batch_count"] == 2
    assert diagnostics["request_count"] == 6
    assert diagnostics["request_limit"] == 8
    assert diagnostics["split_count"] == 2


def test_source_fingerprint_tracks_speaker_profile_and_word_timing():
    draft = VideoLocalizationDraft.model_validate(
        {
            "speakers": [{"speaker_id": "speaker_01", "display_name": "Alex", "notes": "说话直接"}],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Hello",
                    "source_word_ids": ["word_0001"],
                }
            ],
            "transcription": {
                "revision_id": "revision_01",
                "words": [
                    {
                        "word_id": "word_0001",
                        "segment_id": "segment_01",
                        "text": "Hello",
                        "start_ms": 100,
                        "end_ms": 700,
                    }
                ],
            },
        }
    )
    baseline = localization.source_fingerprint(draft)
    changed_speaker = draft.model_copy(
        update={"speakers": [draft.speakers[0].model_copy(update={"notes": "说话犹豫"})]}
    )
    changed_word_timing = draft.model_copy(
        update={
            "transcription": draft.transcription.model_copy(
                update={"words": [draft.transcription.words[0].model_copy(update={"start_ms": 180})]}
            )
        }
    )

    assert localization.source_fingerprint(changed_speaker) != baseline
    assert localization.source_fingerprint(changed_word_timing) != baseline


def test_localization_pipeline_creates_non_one_to_one_dual_text_track(monkeypatch):
    responses = iter(
        [
            {
                "overview": "创作者回顾一次重要变化",
                "era": "现代",
                "setting": "教程口播",
                "topics": ["创作工具"],
                "speakers": [
                    {
                        "speaker_id": "speaker_01",
                        "persona": "直接、兴奋",
                        "speech_habits": "短句",
                        "relationship": "向观众讲解",
                        "emotion": "兴奋",
                    }
                ],
                "style_rules": ["自然口语"],
                "needs_research": False,
                "research_questions": [],
            },
            {"items": [{"id": "bundle_001", "text": "1992 年，这彻底改变了创作者。"}]},
            {"checked_count": 1, "changes": []},
            {"checked_count": 1, "changes": []},
        ]
    )
    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    payloads: list[dict] = []

    def complete_json(**kwargs):
        payloads.append(kwargs["user_payload"])
        return next(responses)

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    progress = []
    previews = []

    run = localization.generate_localization_draft(
        _draft(),
        on_progress=lambda value, stage: progress.append((value, stage)),
        on_preview=lambda phase, cues: previews.append((phase, cues)),
    )

    assert len(run.draft.localized_subtitles) == 1
    subtitle = run.draft.localized_subtitles[0]
    assert subtitle.text == "1992 年 这彻底改变了创作者"
    assert subtitle.tts_text == "1992 年，这彻底改变了创作者。"
    assert subtitle.source_cue_ids == ["cue_0001", "cue_0002"]
    assert subtitle.start_ms == 1000
    assert subtitle.end_ms == 4420
    assert run.draft.cues[0].zh_localized_subtitle_text == subtitle.text
    assert run.draft.cues[0].tts_recommended_text == subtitle.tts_text
    assert run.draft.cues[1].zh_localized_subtitle_text is None
    assert set(run.summary["task_step_results"]) == {
        "prepare_context",
        "research",
        "localize",
        "fit_segments",
        "segment_timing",
        "quality_review",
        "post_review_constraints",
        "write_track",
    }
    quality_metrics = {
        item["label"]: item["value"] for item in run.summary["task_step_results"]["quality_review"]["metrics"]
    }
    assert quality_metrics["计划批次"] == "2"
    assert quality_metrics["模型请求"] == "2"
    assert quality_metrics["失败拆分"] == "0"
    assert [phase for phase, _items in previews] == ["localized_review"]
    assert progress[-1][1] == "本土化字幕初稿已生成，正在保存"
    localize_payload = next(payload for payload in payloads if payload["task"].endswith(":localize"))
    assert localize_payload["document"] == [
        {
            "id": "bundle_001",
            "speaker_id": "speaker_01",
            "source": "In 1992, this changed everything for creators",
        }
    ]
    assert not any("start_ms" in item or "end_ms" in item for item in localize_payload["document"])
    assert "source_word_ids" not in str(localize_payload)


def test_localization_pipeline_uses_one_generation_and_one_sparse_review(monkeypatch):
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                speaker_id="speaker_01",
                start_ms=0,
                end_ms=1000,
                en_subtitle_text="Show the result",
            )
        ]
    )
    tasks: list[str] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        task = payload["task"]
        tasks.append(task)
        if task.endswith(":context"):
            return {
                "overview": "创作者展示结果",
                "topics": [],
                "speakers": [],
                "style_rules": ["自然口语"],
                "needs_research": False,
                "research_questions": [],
            }
        if task.endswith(":localize"):
            assert kwargs["allow_array"] is True
            assert payload["document"] == [
                {"id": "bundle_001", "speaker_id": "speaker_01", "source": "Show the result"}
            ]
            return {"items": [{"id": "bundle_001", "text": "接下来看最终效果。"}]}
        if task.endswith(":quality-review"):
            return {"checked_count": 1, "changes": []}
        if task.endswith(":timed-review-detect"):
            return {"issue_ids": [], "has_more_critical_issues": False}
        raise AssertionError(task)

    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    run = localization.generate_localization_draft(draft)

    assert run.draft.localized_subtitles[0].text == "接下来看最终效果"
    assert sum(task.endswith(":localize") for task in tasks) == 1
    assert sum(task.endswith(":quality-review") for task in tasks) == 1
    assert sum(task.endswith(":timed-review-detect") for task in tasks) == 1
    assert not any(task.endswith(":fit-segments") for task in tasks)
    metrics = {
        item["label"]: item["value"] for item in run.summary["task_step_results"]["post_review_constraints"]["metrics"]
    }
    assert metrics["二次返修"] == "0"
    assert metrics["剩余硬性超限"] == "0"


def test_localization_sparse_review_repairs_changed_numbers_once(monkeypatch):
    responses = iter(
        [
            {
                "overview": "",
                "topics": [],
                "speakers": [],
                "style_rules": [],
                "needs_research": False,
                "research_questions": [],
            },
            {"items": [{"id": "bundle_001", "text": "这彻底改变了创作者。"}]},
            {
                "checked_count": 1,
                "changes": [{"id": "bundle_001", "replacement": "1992 年，这彻底改变了创作者。", "reason": "补回年份"}],
            },
            {"checked_count": 1, "changes": []},
        ]
    )
    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    tasks = []

    def complete_json(**kwargs):
        tasks.append(kwargs["user_payload"]["task"])
        return next(responses)

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    run = localization.generate_localization_draft(_draft())

    assert run.draft.localized_subtitles[0].text.startswith("1992")
    assert sum(task.endswith(":localize") for task in tasks) == 1
    assert sum(task.endswith(":quality-review") for task in tasks) == 1


def test_localization_pipeline_rejects_numbers_when_repair_still_changes_them(monkeypatch):
    draft = _draft().model_copy(update={"cues": [_draft().cues[0]]})
    bad = {"items": [{"id": "bundle_001", "text": "这彻底改变了创作者。"}]}
    responses = iter(
        [
            {
                "overview": "",
                "topics": [],
                "speakers": [],
                "style_rules": [],
                "needs_research": False,
                "research_questions": [],
            },
            bad,
            {
                "checked_count": 1,
                "changes": [{"id": "bundle_001", "replacement": "这彻底改变了创作者。", "reason": "未补回数字"}],
            },
        ]
    )
    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    monkeypatch.setattr(localization.llm_runtime, "complete_json", lambda **_kwargs: next(responses))

    with pytest.raises(AppException) as exc_info:
        localization.generate_localization_draft(draft)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_NUMBER_CHANGED"


def test_localization_pipeline_keeps_a_medium_document_in_one_request(monkeypatch):
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id=f"cue_{index:04d}",
                speaker_id="speaker_01",
                start_ms=index * 1000,
                end_ms=index * 1000 + 800,
                en_subtitle_text=f"Source line {chr(96 + index)}",
            )
            for index in range(1, 5)
        ]
    )
    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    localize_calls: list[list[str]] = []
    quality_review_calls: list[list[str]] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        task = payload["task"]
        if task.endswith(":context"):
            return {
                "overview": "",
                "topics": [],
                "speakers": [],
                "style_rules": [],
                "needs_research": False,
                "research_questions": [],
            }
        if task.endswith(":localize"):
            localize_calls.append([item["id"] for item in payload["document"]])
            return {"items": [{"id": "bundle_001", "text": "译文甲，译文乙，译文丙，译文丁。"}]}
        if task.endswith(":quality-review"):
            item_ids = [item["id"] for item in payload["document"]]
            quality_review_calls.append(item_ids)
            return {"checked_count": len(item_ids), "changes": []}
        if task.endswith(":timed-review-detect"):
            return {"issue_ids": [], "has_more_critical_issues": False}
        raise AssertionError(task)

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    progress: list[tuple[float, str]] = []

    run = localization.generate_localization_draft(
        draft,
        on_progress=lambda value, stage: progress.append((value, stage)),
    )

    assert localize_calls == [["bundle_001"]]
    assert len(run.draft.localized_subtitles) >= 1
    assert not any("拆分" in stage for _value, stage in progress)
    assert quality_review_calls == [["bundle_001"]]
    quality_metrics = {
        item["label"]: item["value"] for item in run.summary["task_step_results"]["quality_review"]["metrics"]
    }
    assert quality_metrics["模型请求"] == "2"
    assert quality_metrics["失败拆分"] == "0"


def test_timed_review_keeps_a_medium_timeline_in_one_compact_request(monkeypatch):
    timed = [_timed_item(index) for index in range(130)]
    timed[60]["start_ms"] = timed[59]["end_ms"] + 1800
    calls: list[dict] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        calls.append(payload)
        return {"checked_count": len(payload["items"]), "changes": []}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert len(calls) == 1
    assert len(calls[0]["items"]) == 130
    assert diagnostics["planned_batch_count"] == 1
    assert diagnostics["request_count"] == 1
    assert [item["id"] for item in reviewed] == [item["id"] for item in timed]
    assert changes == []


def test_timed_review_uses_one_sparse_window_for_a_long_timeline(monkeypatch):
    timed = [_timed_item(index) for index in range(localization.TIMED_REVIEW_BATCH_MAX_ITEMS + 1)]
    calls: list[dict] = []

    def complete_json(**kwargs):
        calls.append(kwargs["user_payload"])
        return {"issue_ids": [], "has_more_critical_issues": False}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    assert len(calls) == 1
    assert len(calls[0]["items"]) == localization.TIMED_REVIEW_BATCH_MAX_ITEMS
    assert diagnostics["request_count"] == 1
    assert diagnostics["timed_review_mode"] == "llm_risk_window"
    assert diagnostics["reviewed_item_count"] == localization.TIMED_REVIEW_BATCH_MAX_ITEMS
    assert calls[0]["coverage"]["mode"] == "risk_window"
    assert calls[0]["coverage"]["total_item_count"] == len(timed)


def test_timed_review_long_timeline_window_follows_high_risk_item(monkeypatch):
    timed = [_timed_item(index) for index in range(localization.TIMED_REVIEW_BATCH_MAX_ITEMS * 2)]
    timed[-1]["quality_flags"] = ["semantic_mapping_review"]
    calls: list[dict] = []

    def complete_json(**kwargs):
        calls.append(kwargs["user_payload"])
        return {"issue_ids": [], "has_more_critical_issues": False}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    assert calls[0]["items"][-1]["id"] == timed[-1]["id"]
    assert diagnostics["reviewed_start_index"] == len(timed) - localization.TIMED_REVIEW_BATCH_MAX_ITEMS


def test_timed_review_falls_back_to_local_checks_when_sparse_ids_are_truncated(monkeypatch):
    timed = [_timed_item(index) for index in range(20)]

    def complete_json(**_kwargs):
        raise localization.llm_runtime.LlmRuntimeError(
            "truncated",
            code="llm_output_truncated",
            status_code=502,
        )

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    assert diagnostics["request_count"] == 1
    assert diagnostics["fallback_count"] == 1
    assert diagnostics["timed_review_mode"] == "deterministic_fallback"


def test_timed_review_detects_on_full_timeline_then_repairs_only_reported_ids(monkeypatch):
    timed = [_timed_item(index) for index in range(40)]
    timed[20].update(
        end_ms=timed[20]["start_ms"] + 5000,
        source_text="He snaps his fingers and the scene changes",
        display_text="画面变了 他打了个响指",
        tts_text="画面变了，他打了个响指。",
    )
    calls: list[dict] = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        calls.append(kwargs)
        if payload["task"].endswith("timed-review-detect"):
            return {"issue_ids": ["localized_0021"], "has_more_critical_issues": False}
        assert payload["task"].endswith("timed-review-repair")
        assert payload["issue_ids"] == ["localized_0021"]
        assert [item["id"] for item in payload["editable_items"]] == ["localized_0021"]
        assert len(payload["ordered_context"]) == 7
        return {"changes": [{"id": "localized_0021", "text": "他打了个响指，画面立刻变了。", "reason": "修正语义顺序"}]}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert len(calls) == 2
    assert len(calls[0]["user_payload"]["items"]) == 40
    assert calls[0]["disable_reasoning"] is True
    assert calls[1]["disable_reasoning"] is True
    assert reviewed[20]["display_text"] == "他打了个响指 画面立刻变了"
    assert len(changes) == 1
    assert diagnostics["request_count"] == 2


def test_timed_review_sends_missing_question_function_to_llm_repair(monkeypatch):
    timed = [_timed_item(0)]
    timed[0].update(
        end_ms=timed[0]["start_ms"] + 5000,
        source_text="Insane, right? Then I keep talking.",
        display_text="然后我继续说话",
        tts_text="然后我继续说话。",
    )
    tasks = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        tasks.append(payload["task"])
        if payload["task"].endswith("timed-review-detect"):
            assert payload["items"][0]["review_focus"]
            return {"issue_ids": [], "has_more_critical_issues": False}
        assert payload["issue_ids"] == ["localized_0001"]
        return {
            "changes": [
                {
                    "id": "localized_0001",
                    "text": "很夸张吧？然后我继续说话。",
                    "reason": "补回反问语气",
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert len(tasks) == 2
    assert reviewed[0]["display_text"] == "很夸张吧？然后我继续说话"
    assert changes[0]["reason"] == "补回反问语气"
    assert diagnostics["request_count"] == 2


def test_bundle_review_requires_repair_when_chinese_borrows_next_question(monkeypatch):
    bundles = [
        {
            "id": "bundle_001",
            "source": "I can put something behind me that definitely should not be there.",
            "text": "我可以在身后放一个绝对不该出现的东西，离谱吧？",
        }
    ]
    calls = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        calls.append(payload)
        assert payload["document"][0]["must_repair_speech_act"] is True
        return {
            "checked_count": 1,
            "changes": [
                {
                    "id": "bundle_001",
                    "replacement": "我可以在身后放一个绝对不该出现的东西",
                    "reason": "移除提前的反应",
                }
            ],
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_localized_bundles(
        bundles,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert len(calls) == 1
    assert reviewed[0]["text"] == "我可以在身后放一个绝对不该出现的东西"
    assert changes[0]["reason"] == "移除提前的反应"
    assert diagnostics["request_count"] == 1


def test_timed_review_requires_repair_when_chinese_borrows_next_question(monkeypatch):
    timed = [_timed_item(0)]
    timed[0].update(
        end_ms=timed[0]["start_ms"] + 5000,
        source_text="I can put something behind me that definitely should not be there.",
        display_text="我可以在身后放一个绝对不该出现的东西 离谱吧？",
        tts_text="我可以在身后放一个绝对不该出现的东西，离谱吧？",
    )
    tasks = []

    def complete_json(**kwargs):
        payload = kwargs["user_payload"]
        tasks.append(payload["task"])
        if payload["task"].endswith("timed-review-detect"):
            assert payload["items"][0]["review_focus"]
            return {"issue_ids": [], "has_more_critical_issues": False}
        assert payload["issue_ids"] == ["localized_0001"]
        return {
            "changes": [
                {
                    "id": "localized_0001",
                    "text": "我可以在身后放一个绝对不该出现的东西。",
                    "reason": "移除提前的反应",
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert tasks == [
        f"{localization.LOCALIZATION_PROMPT_VERSION}:timed-review-detect",
        f"{localization.LOCALIZATION_PROMPT_VERSION}:timed-review-repair",
    ]
    assert reviewed[0]["display_text"] == "我可以在身后放一个绝对不该出现的东西"
    assert changes[0]["reason"] == "移除提前的反应"
    assert diagnostics["request_count"] == 2


def test_timed_review_can_redistribute_meaning_across_adjacent_subtitles(monkeypatch):
    timed = [_timed_item(0), _timed_item(1)]
    timed[0].update(source_text="He snaps his fingers", display_text="画面已经变了", tts_text="画面已经变了。")
    timed[1].update(source_text="and the scene changes", display_text="他打了个响指", tts_text="他打了个响指。")

    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "checked_count": 2,
            "changes": [
                {"id": "localized_0001", "text": "他打了个响指。", "reason": "修正语义错位"},
                {"id": "localized_0002", "text": "画面立刻变了。", "reason": "修正语义错位"},
            ],
        },
    )

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert [item["tts_text"] for item in reviewed] == ["他打了个响指。", "画面立刻变了。"]
    assert [item["id"] for item in reviewed] == ["localized_0001", "localized_0002"]
    assert len(changes) == 2


def test_timed_review_does_not_trust_a_model_reported_checked_count(monkeypatch):
    timed = [_timed_item(0), _timed_item(1)]
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"checked_count": 1, "changes": []},
    )

    reviewed, changes, diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    assert diagnostics["request_count"] == 1


def test_timed_review_stops_when_model_reports_more_critical_issues(monkeypatch):
    timed = [_timed_item(0)]
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"changes": [], "has_more_critical_issues": True},
    )

    with pytest.raises(AppException) as exc_info:
        localization._review_timed_localization(timed, profile_id="llm_default", is_cancelled=None)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TIMED_REVIEW_OVERFLOW"


def test_timed_review_rejects_changes_outside_the_editable_batch(monkeypatch):
    timed = [_timed_item(0)]
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "checked_count": 1,
            "changes": [{"id": "localized_9999", "text": "越界修改。", "reason": "错误"}],
        },
    )

    with pytest.raises(AppException) as exc_info:
        localization._review_timed_localization(timed, profile_id="llm_default", is_cancelled=None)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_REVIEW_INVALID"


def test_timed_review_reverts_a_change_that_alters_numbers(monkeypatch):
    timed = [_timed_item(0)]
    timed[0].update(source_text="Version 2.0", display_text="2.0 版本", tts_text="2.0 版本。")
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "checked_count": 1,
            "changes": [{"id": "localized_0001", "text": "3.0 版本。", "reason": "错误改数"}],
        },
    )

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []


def test_timed_review_ignores_a_change_that_creates_a_hard_subtitle_violation(monkeypatch):
    timed = [_timed_item(0)]
    timed[0].update(
        start_ms=0,
        end_ms=5000,
        source_text="A concise source sentence",
        display_text="这句原本长度合适",
        tts_text="这句原本长度合适。",
    )
    overlong = "这是一条被时间线终审扩写得明显过长并且超过单条字幕三十二个字硬限制的中文表达"
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "changes": [{"id": "localized_0001", "text": overlong, "reason": "错误扩写"}],
            "has_more_critical_issues": False,
        },
    )

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []


def test_timed_review_reverts_numbers_swapped_between_subtitles(monkeypatch):
    timed = [_timed_item(0), _timed_item(1)]
    timed[0].update(source_text="The budget is 10 dollars", display_text="预算是 10 美元", tts_text="预算是 10 美元。")
    timed[1].update(source_text="It took 20 days", display_text="用了 20 天", tts_text="用了 20 天。")
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "checked_count": 2,
            "changes": [
                {"id": "localized_0001", "text": "预算是 20 美元。", "reason": "错误交换"},
                {"id": "localized_0002", "text": "用了 10 天。", "reason": "错误交换"},
            ],
        },
    )

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []


def test_timed_review_still_rejects_a_preexisting_unresolved_number_loss(monkeypatch):
    timed = [_timed_item(0)]
    timed[0].update(source_text="Version 2.0", display_text="这个版本", tts_text="这个版本。")
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"changes": [], "has_more_critical_issues": False},
    )

    with pytest.raises(AppException) as exc_info:
        localization._review_timed_localization(timed, profile_id="llm_default", is_cancelled=None)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_NUMBER_CHANGED"


def test_timed_review_allows_a_preexisting_number_shift_to_be_fixed_as_one_cluster(monkeypatch):
    timed = [_timed_item(0), _timed_item(1)]
    timed[0].update(
        source_text="Created with Seedance 2.", display_text="这是用 Seedance", tts_text="这是用 Seedance，"
    )
    timed[1].update(source_text="0 in 4K", display_text="2.0 的 4K 画面", tts_text="2.0 的 4K 画面。")
    payloads = []

    def complete_json(**kwargs):
        payloads.append(kwargs["user_payload"])
        return {"changes": [], "has_more_critical_issues": False}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    number_groups = [item["number_mapping_group"] for item in payloads[0]["items"]]
    assert number_groups[0] == number_groups[1]
    assert number_groups[0]["required_numbers"] == ["2.0", "4K"]
    assert all(item["required_numbers"] == [] for item in payloads[0]["items"])


def test_timed_review_keeps_number_mapping_across_a_number_free_bundle_item(monkeypatch):
    timed = [_timed_item(0), _timed_item(1), _timed_item(2)]
    for item in timed:
        item["source_bundle_id"] = "bundle_001"
    timed[0].update(source_text="The budget is 10 dollars", display_text="预算", tts_text="预算")
    timed[1].update(source_text="for the animation", display_text="动画部分", tts_text="动画部分")
    timed[2].update(
        source_text="and it takes 20 days",
        display_text="要 10 美元 做 20 天",
        tts_text="要 10 美元，做 20 天",
    )
    payloads = []

    def complete_json(**kwargs):
        payloads.append(kwargs["user_payload"])
        return {"changes": [], "has_more_critical_issues": False}

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    reviewed, changes, _diagnostics = localization._review_timed_localization(
        timed,
        profile_id="llm_default",
        is_cancelled=None,
    )

    assert reviewed == timed
    assert changes == []
    number_groups = [item["number_mapping_group"] for item in payloads[0]["items"]]
    assert all(group == number_groups[0] for group in number_groups)
    assert number_groups[0]["required_numbers"] == ["10", "20"]


def test_final_localized_timeline_gate_rejects_unresolved_reading_speed():
    item = _timed_item(0)
    item.update(
        start_ms=0,
        end_ms=1000,
        display_text="这是一条终审后仍然明显太长的中文字幕",
        tts_text="这是一条终审后仍然明显太长的中文字幕。",
        source_cue_ids=["cue_0001"],
    )

    with pytest.raises(AppException) as exc_info:
        localization._ensure_localized_timeline_constraints([item])

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TIMING_BUDGET_UNRESOLVED"


def test_final_localized_timeline_gate_allows_target_overage_below_hard_limit():
    item = _timed_item(0)
    item.update(
        start_ms=0,
        end_ms=833,
        display_text="一二三四五六七八九十",
        tts_text="一二三四五六七八九十。",
        source_cue_ids=["cue_0001"],
    )

    assert localization._candidate_exceeds_budget(item)
    assert not localization._candidate_exceeds_hard_budget(item)
    localization._ensure_localized_timeline_constraints([item])


def test_local_reading_speed_compression_preserves_meaning_and_numbers():
    item = _timed_item(0)
    item.update(
        start_ms=0,
        end_ms=1300,
        source_text="Version 2.0 can already be used directly",
        display_text="这个 2.0 版本已经可以直接使用",
        tts_text="这个 2.0 版本已经可以直接使用。",
    )

    replacement = localization._compress_candidate_locally(item, item)

    assert replacement is not None
    assert replacement["display_text"] == "这个 2.0 版本已经能直接使用"
    assert "2.0" in replacement["display_text"]
    assert not localization._candidate_exceeds_budget({**item, **replacement})


def test_local_reading_speed_compression_does_not_create_transition_echo():
    item = _timed_item(0)
    item.update(
        start_ms=0,
        end_ms=1160,
        source_text="And there is so much more I can do with it",
        display_text="而且我还能用它做更多事情",
        tts_text="而且我还能用它做更多事情，",
    )

    replacement = localization._compress_candidate_locally(item, item)

    assert replacement is not None
    assert replacement["display_text"] == "我还能用它做更多事情"
    assert "还我还" not in replacement["display_text"]
    assert not localization._candidate_exceeds_budget({**item, **replacement})


@pytest.mark.parametrize(
    ("text", "expected", "duration_ms"),
    [
        ("我做这个视频的原因是", "原因是", 880),
        ("我做这期视频的原因是", "原因是", 880),
        ("看起来像电影里出来的效果", "就像电影里的效果", 1220),
        ("并在过程中改变光照", "同时改变光照", 833),
        ("好了 这就是向你的镜头添加东西", "好了 这就是给镜头加东西", 1280),
        ("已经知道我想在它里面看到什么", "已经知道我想在里面看到什么", 1380),
    ],
)
def test_local_reading_speed_compression_removes_common_spoken_redundancy(text, expected, duration_ms):
    item = _timed_item(0)
    item.update(
        start_ms=0,
        end_ms=duration_ms,
        source_text="Generic source without numbers",
        display_text=text,
        tts_text=text,
    )

    replacement = localization._compress_candidate_locally(item, item)

    assert replacement is not None
    assert replacement["display_text"] == expected
    assert not localization._candidate_exceeds_budget({**item, **replacement})


def test_readability_extension_borrows_only_a_small_leading_gap_when_needed():
    timed = [
        {**_timed_item(0), "start_ms": 0, "end_ms": 900, "display_text": "前一句"},
        {**_timed_item(1), "start_ms": 1000, "end_ms": 1833, "display_text": "如果按老方法做呢"},
        {**_timed_item(2), "start_ms": 1833, "end_ms": 3000, "display_text": "后一句"},
    ]

    localization._extend_timed_for_readability(timed, _draft())

    assert timed[1]["start_ms"] == 990
    assert timed[1]["end_ms"] == 1833
    assert localization._candidate_budget_report(timed[1])["cps"] <= localization.MAX_CHINESE_CPS


def test_localization_reuses_collapsed_alignment_repair_without_mutating_source_draft():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for index, (text, start_ms, end_ms) in enumerate(
            [
                ("That's", 1000, 1001),
                ("months", 1001, 1002),
                ("work", 1002, 1003),
                ("next", 3000, 3300),
            ]
        )
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=1000,
                end_ms=1003,
                en_subtitle_text="That's months work",
                source_word_ids=[word.word_id for word in words[:3]],
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                start_ms=3000,
                end_ms=3300,
                en_subtitle_text="next",
                source_word_ids=[words[3].word_id],
            ),
        ],
        transcription=VideoLocalizationTranscriptionState(
            words=words,
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="segment_0001",
                    start_ms=1000,
                    end_ms=3300,
                    raw_text="That's months work next",
                )
            ],
        ),
    )

    repaired = localization._draft_with_repaired_alignment_timing(draft)

    assert repaired.transcription.words[2].end_ms == 3000
    assert repaired.transcription.words[0].timing_source == "asr_segment_interpolation"
    assert draft.transcription.words[2].end_ms == 1003


def test_localization_number_check_rejoins_split_decimal_word_tokens():
    cue = VideoLocalizationCue(
        cue_id="cue_0001",
        start_ms=0,
        end_ms=1400,
        en_subtitle_text="2.0 in 4K",
        source_word_ids=["word_0001", "word_0002", "word_0003", "word_0004"],
    )
    words = [
        VideoLocalizationAlignedWord(word_id="word_0001", segment_id="segment_1", text="2.", start_ms=0, end_ms=200),
        VideoLocalizationAlignedWord(word_id="word_0002", segment_id="segment_1", text="0", start_ms=200, end_ms=400),
        VideoLocalizationAlignedWord(word_id="word_0003", segment_id="segment_1", text="in", start_ms=400, end_ms=700),
        VideoLocalizationAlignedWord(
            word_id="word_0004", segment_id="segment_1", text="4K.", start_ms=700, end_ms=1200
        ),
    ]
    draft = VideoLocalizationDraft(
        cues=[cue],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )
    word_by_id = {word.word_id: word for word in words}

    result = localization._validate_localized_segments(
        [
            {
                "source_cue_ids": [cue.cue_id],
                "source_word_ids": cue.source_word_ids,
                "display_text": "这是 Seedance 2.0的4K效果",
                "tts_text": "这是 Seedance 2.0的4K效果。",
            }
        ],
        draft.cues,
        {cue.cue_id},
        set(cue.source_word_ids),
        {cue.cue_id: cue},
        word_by_id,
    )

    assert result[0]["source_text"] == "2.0 in 4K."
    assert localization._normalized_numbers(result[0]["display_text"]) == {"2.0": 1, "4K": 1}


def test_semantic_source_join_rejoins_decimal_when_period_starts_next_segment():
    assert localization._join_semantic_sources(["Seedance 2", ".0 in 4K."]) == "Seedance 2.0 in 4K."
    assert localization._normalized_numbers(localization._join_semantic_sources(["Seedance 2", ".0 in 4K."])) == {
        "2.0": 1,
        "4K": 1,
    }


def test_post_review_merge_keeps_degree_adverb_with_negative_predicate():
    left = _timed_item(0)
    right = _timed_item(1)
    left.update(
        id="localized_0004",
        source_text="I can put something behind me",
        display_text="比如在我身后放一个绝对",
        tts_text="比如在我身后放一个绝对",
        source_cue_ids=["cue_0005"],
        source_word_ids=["word_0001"],
    )
    right.update(
        id="localized_0005",
        source_text="that definitely should not be there",
        display_text="不该出现的东西",
        tts_text="不该出现的东西。",
        source_cue_ids=["cue_0006"],
        source_word_ids=["word_0002"],
    )

    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([left, right])

    assert len(merged) == 1
    assert merged[0]["display_text"] == "比如在我身后放一个绝对不该出现的东西"
    assert merged[0]["tts_text"] == "比如在我身后放一个绝对不该出现的东西。"
    assert merged[0]["source_cue_ids"] == ["cue_0005", "cue_0006"]
    assert merged[0]["source_word_ids"] == ["word_0001", "word_0002"]
    assert "semantic_boundary_merged" in merged[0]["quality_flags"]
    assert changes[0]["reason"] == "避免切断紧密中文结构"
    assert id_map == {"localized_0005": "localized_0004"}


def test_post_review_merge_keeps_example_lead_in_with_following_subtitle():
    left = _timed_item(0)
    right = _timed_item(1)
    left.update(
        id="localized_0005",
        source_text="For example",
        display_text="比如",
        tts_text="比如",
        source_cue_ids=["cue_0005"],
        source_word_ids=["word_0001"],
        adaptation_note="引出例子",
        timing_source="逐词时间",
        research_usage=[{"question_id": "research_01", "effect": "确认术语"}],
    )
    right.update(
        id="localized_0006",
        source_text="I can put something behind me",
        display_text="我可以在身后放点东西",
        tts_text="我可以在身后放点东西。",
        source_cue_ids=["cue_0006"],
        source_word_ids=["word_0002"],
        adaptation_note="保持口语",
        timing_source="ASR 字幕范围",
        research_usage=[
            {"question_id": "research_01", "effect": "确认术语"},
            {"question_id": "research_02", "effect": "确认背景"},
        ],
    )

    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([left, right])

    assert len(merged) == 1
    assert merged[0]["display_text"] == "比如我可以在身后放点东西"
    assert merged[0]["source_cue_ids"] == ["cue_0005", "cue_0006"]
    assert merged[0]["adaptation_note"] == "引出例子；保持口语"
    assert merged[0]["timing_source"] == "逐词时间 + ASR 字幕范围"
    assert merged[0]["research_usage"] == [
        {"question_id": "research_01", "effect": "确认术语"},
        {"question_id": "research_02", "effect": "确认背景"},
    ]
    assert changes[0]["reason"] == "避免切断紧密中文结构"
    assert id_map == {"localized_0006": "localized_0005"}


def test_post_review_merge_does_not_merge_complete_or_distant_subtitles():
    complete = _timed_item(0)
    nearby = _timed_item(1)
    complete.update(display_text="不过", tts_text="不过。")
    nearby.update(display_text="那就继续", tts_text="那就继续。")

    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([complete, nearby])

    assert len(merged) == 2
    assert not changes
    assert not id_map

    distant = {**nearby, "start_ms": complete["end_ms"] + 1200, "end_ms": complete["end_ms"] + 2200}
    complete.update(display_text="比如", tts_text="比如")
    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([complete, distant])

    assert len(merged) == 2
    assert not changes
    assert not id_map


def test_post_review_merge_does_not_chain_across_three_subtitles():
    first = _timed_item(0)
    second = _timed_item(1)
    third = _timed_item(2)
    first.update(display_text="比如", tts_text="比如")
    second.update(display_text="例如", tts_text="例如")
    third.update(display_text="这里放个东西", tts_text="这里放个东西。")

    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([first, second, third])

    assert [item["display_text"] for item in merged] == ["比如", "例如这里放个东西"]
    assert len(changes) == 1
    assert id_map == {third["id"]: second["id"]}


def test_post_review_merge_rejects_cross_speaker_boundary():
    draft = _draft()
    draft.cues[1] = draft.cues[1].model_copy(update={"speaker_id": "speaker_02"})
    left = _timed_item(0)
    right = _timed_item(1)
    left.update(
        display_text="比如",
        tts_text="比如",
        source_cue_ids=["cue_0001"],
        source_word_ids=[],
    )
    right.update(
        display_text="这里放个东西",
        tts_text="这里放个东西。",
        source_cue_ids=["cue_0002"],
        source_word_ids=[],
    )

    merged, changes, id_map = localization._merge_unsafe_localized_boundaries([left, right], draft=draft)

    assert len(merged) == 2
    assert not changes
    assert not id_map


def test_review_focus_flags_unnatural_shot_classifier():
    assert localization._localized_bundle_review_focus("This is the same shot.", "这是同一条镜头")
    assert localization._timed_localization_review_focus(
        {
            "source_text": "This is the same shot.",
            "display_text": "这是同一条镜头",
            "tts_text": "这是同一条镜头。",
        }
    )


def test_review_focus_flags_literal_reaction_and_4k_phrasing():
    source = "Insane, right? This is the same shot mixed with Seedance 2.0 in 4K."
    chinese = "疯狂 对吧？这是同一个镜头 用Seedance 2.0在4K下混合"
    context = {"content_type": "technology_tutorial"}

    bundle_focus = localization._localized_bundle_review_focus(source, chinese, context)
    timed_focus = localization._timed_localization_review_focus(
        {"source_text": source, "display_text": chinese, "tts_text": chinese},
        context,
    )

    assert any("机械翻译" in item for item in bundle_focus)
    assert any("4K" in item for item in bundle_focus)
    assert any("机械翻译" in item for item in timed_focus)
    assert any("4K" in item for item in timed_focus)


def test_review_focus_flags_model_and_output_spec_attached_to_the_wrong_object():
    source = "This is the same shot mixed with Acme 3.1 in 4K."
    chinese = "这是同一个画面混了Acme 3.1的4K画质"

    focus = localization._localized_bundle_review_focus(
        source,
        chinese,
        {"content_type": "technology_tutorial"},
    )

    assert any("输出规格" in item for item in focus)
    assert any("工具/模型还是素材" in item for item in focus)


def test_review_focus_flags_literal_english_possessive_in_self_action():
    source = "Set my own head on fire and keep talking."
    chinese = "把我的脑袋点上火 然后继续讲"

    assert any("my own" in item for item in localization._localized_bundle_review_focus(source, chinese))
    assert localization._timed_localization_review_focus(
        {"source_text": source, "display_text": chinese, "tts_text": chinese}
    )


def test_localization_mapping_rejects_cross_speaker_and_missing_words():
    draft = VideoLocalizationDraft.model_validate(
        {
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 900,
                    "en_subtitle_text": "Hello",
                    "source_word_ids": ["word_0001"],
                },
                {
                    "cue_id": "cue_0002",
                    "speaker_id": "speaker_02",
                    "start_ms": 900,
                    "end_ms": 1800,
                    "en_subtitle_text": "there",
                    "source_word_ids": ["word_0002"],
                },
            ],
            "transcription": {
                "words": [
                    {"word_id": "word_0001", "segment_id": "segment_01", "text": "Hello", "start_ms": 0, "end_ms": 800},
                    {
                        "word_id": "word_0002",
                        "segment_id": "segment_02",
                        "text": "there",
                        "start_ms": 900,
                        "end_ms": 1700,
                    },
                ]
            },
        }
    )
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    word_by_id = {word.word_id: word for word in draft.transcription.words}

    with pytest.raises(AppException) as cross_speaker:
        localization._validate_localized_segments(
            [
                {
                    "source_cue_ids": ["cue_0001", "cue_0002"],
                    "source_word_ids": ["word_0001", "word_0002"],
                    "display_text": "大家好",
                    "tts_text": "大家好。",
                }
            ],
            draft.cues,
            set(cue_by_id),
            set(word_by_id),
            cue_by_id,
            word_by_id,
        )
    assert cross_speaker.value.code == "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID"

    same_speaker_cues = [cue.model_copy(update={"speaker_id": "speaker_01"}) for cue in draft.cues]
    same_speaker_by_id = {cue.cue_id: cue for cue in same_speaker_cues}
    with pytest.raises(AppException) as missing_word:
        localization._validate_localized_segments(
            [
                {
                    "source_cue_ids": ["cue_0001", "cue_0002"],
                    "source_word_ids": ["word_0001"],
                    "display_text": "大家好",
                    "tts_text": "大家好。",
                }
            ],
            same_speaker_cues,
            set(same_speaker_by_id),
            set(word_by_id),
            same_speaker_by_id,
            word_by_id,
        )
    assert missing_word.value.code == "VIDEO_LOCALIZATION_SOURCE_MAPPING_INVALID"


def test_finalize_timing_borrows_neighbor_duration_for_minimum_display_time():
    items = [
        {
            "id": "localized_0001",
            "start_ms": 100,
            "end_ms": 2000,
            "display_text": "上一条字幕",
            "quality_flags": ["first_only"],
        },
        {
            "id": "localized_0002",
            "start_ms": 2000,
            "end_ms": 2008,
            "display_text": "中央字幕",
            "quality_flags": [],
        },
        {
            "id": "localized_0003",
            "start_ms": 2008,
            "end_ms": 4000,
            "display_text": "下一条字幕",
            "quality_flags": [],
        },
    ]

    finalized = localization._finalize_timing(items, VideoLocalizationDraft())

    assert all(item["end_ms"] - item["start_ms"] >= localization.MIN_SUBTITLE_DURATION_MS for item in finalized)
    assert all(current["start_ms"] >= previous["end_ms"] for previous, current in zip(finalized, finalized[1:]))
    assert "first_only" in finalized[0]["quality_flags"]
    assert "first_only" not in finalized[1]["quality_flags"]
    assert "first_only" not in finalized[2]["quality_flags"]


def test_finalize_timing_propagates_short_duration_into_later_gap():
    draft = VideoLocalizationDraft.model_validate({"source_media": {"duration_ms": 2833}})
    items = [
        {"id": "localized_0001", "start_ms": 0, "end_ms": 33, "display_text": "一", "quality_flags": []},
        {"id": "localized_0002", "start_ms": 33, "end_ms": 866, "display_text": "二", "quality_flags": []},
        {"id": "localized_0003", "start_ms": 2000, "end_ms": 2833, "display_text": "三", "quality_flags": []},
    ]

    finalized = localization._finalize_timing(items, draft)

    assert [(item["start_ms"], item["end_ms"]) for item in finalized] == [
        (0, 833),
        (833, 1666),
        (2000, 2833),
    ]


def test_finalize_timing_rejects_truly_dense_media_range():
    draft = VideoLocalizationDraft.model_validate({"source_media": {"duration_ms": 2000}})
    items = [
        {
            "id": f"localized_{index:04d}",
            "start_ms": index * 33,
            "end_ms": index * 33 + 33,
            "display_text": "字幕",
            "quality_flags": [],
        }
        for index in range(3)
    ]

    with pytest.raises(AppException) as exc_info:
        localization._finalize_timing(items, draft)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TIMING_TOO_DENSE"


def test_fit_candidate_segments_refines_only_items_over_budget(monkeypatch):
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=4000,
                en_subtitle_text="First complete idea",
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                start_ms=4100,
                end_ms=9000,
                en_subtitle_text="Second complete idea",
            ),
        ]
    )
    candidate = {
        "id": "localized_0001",
        "source_cue_ids": ["cue_0001", "cue_0002"],
        "source_word_ids": [],
        "source_text": "First complete idea Second complete idea",
        "display_text": "这是第一段完整内容接下来是第二段完整内容需要按阅读时间拆开显示",
        "tts_text": "这是第一段完整内容。接下来是第二段完整内容，需要按阅读时间拆开显示。",
        "adaptation_note": "保留完整语义",
        "quality_flags": [],
    }

    def complete_json(**kwargs):
        assert kwargs["user_payload"]["task"].endswith(":fit-segments")
        current = kwargs["user_payload"]["items"][0]["current"]
        assert current["violations"] == ["时长超过8秒，需要按完整语义拆分"]
        assert current["suggested_min_segments"] == 2
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_cue_id": "cue_0001",
                            "display_text": "这是第一段完整内容",
                            "tts_text": "这是第一段完整内容。",
                        },
                        {
                            "end_cue_id": "cue_0002",
                            "display_text": "接下来是第二段完整内容",
                            "tts_text": "接下来是第二段完整内容。",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        [candidate],
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert [item["source_cue_ids"] for item in fitted] == [["cue_0001"], ["cue_0002"]]
    timed = localization._time_candidates(fitted, draft)
    assert all(not localization._candidate_exceeds_budget(item) for item in timed)


def test_fit_candidate_segments_uses_stable_batches_and_compact_boundaries(monkeypatch):
    cues = []
    candidates = []
    for index in range(33):
        left_id = f"cue_{index:04d}_a"
        right_id = f"cue_{index:04d}_b"
        cues.extend(
            [
                VideoLocalizationCue(
                    cue_id=left_id,
                    start_ms=index * 10_000,
                    end_ms=index * 10_000 + 4_000,
                    en_subtitle_text="First complete idea",
                ),
                VideoLocalizationCue(
                    cue_id=right_id,
                    start_ms=index * 10_000 + 4_100,
                    end_ms=index * 10_000 + 8_400,
                    en_subtitle_text="Second complete idea",
                ),
            ]
        )
        candidates.append(
            {
                "id": f"localized_{index:04d}",
                "source_cue_ids": [left_id, right_id],
                "source_word_ids": [],
                "source_text": "First complete idea Second complete idea",
                "display_text": "第一段完整内容接着是第二段完整内容",
                "tts_text": "第一段完整内容，接着是第二段完整内容。",
                "adaptation_note": "保留两段语义",
                "quality_flags": [],
            }
        )
    calls = []

    def complete_json(**kwargs):
        calls.append(len(kwargs["user_payload"]["items"]))
        assert kwargs["user_payload"]["boundary_contract"]["source_mapping"].startswith("不要返回")
        return {
            "items": [
                {
                    "parent_id": item["parent_id"],
                    "segments": [
                        {
                            "end_cue_id": item["source_cues"][0]["cue_id"],
                            "display_text": "第一段完整内容",
                            "tts_text": "第一段完整内容。",
                        },
                        {
                            "end_cue_id": item["source_cues"][1]["cue_id"],
                            "display_text": "接着是第二段完整内容",
                            "tts_text": "接着是第二段完整内容。",
                        },
                    ],
                }
                for item in kwargs["user_payload"]["items"]
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    diagnostics = {}
    progress = []
    previews = []

    fitted = localization._fit_candidate_segments(
        candidates,
        VideoLocalizationDraft(cues=cues),
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
        on_progress=lambda *items: progress.append(items),
        on_preview=previews.append,
        diagnostics=diagnostics,
    )

    assert calls == [33]
    assert len(fitted) == 66
    assert diagnostics["request_count"] == 1
    assert len(diagnostics["rounds"]) == 1
    assert diagnostics["rounds"][0] | {"duration_ms": 0} == {
        "round": 1,
        "problem_count": 33,
        "batch_count": 1,
        "duration_ms": 0,
    }
    assert len(previews) == 1
    assert progress[0][:3] == (0, 1, 1)


def test_fit_batch_splits_after_invalid_json(monkeypatch):
    cues = []
    candidates = []
    for index in range(2):
        left_id = f"cue_{index:04d}_a"
        right_id = f"cue_{index:04d}_b"
        cues.extend(
            [
                VideoLocalizationCue(
                    cue_id=left_id,
                    start_ms=index * 10_000,
                    end_ms=index * 10_000 + 4_000,
                    en_subtitle_text="First complete idea",
                ),
                VideoLocalizationCue(
                    cue_id=right_id,
                    start_ms=index * 10_000 + 4_100,
                    end_ms=index * 10_000 + 8_100,
                    en_subtitle_text="Second complete idea",
                ),
            ]
        )
        candidates.append(
            {
                "id": f"localized_{index:04d}",
                "source_cue_ids": [left_id, right_id],
                "source_word_ids": [],
                "source_text": "First complete idea Second complete idea",
                "display_text": "第一段完整内容接着是第二段完整内容",
                "tts_text": "第一段完整内容，接着是第二段完整内容。",
                "adaptation_note": "保留两段语义",
                "quality_flags": [],
            }
        )
    draft = VideoLocalizationDraft(cues=cues)
    timed = localization._time_candidates(candidates, draft)
    calls: list[int] = []

    def complete_json(**kwargs):
        items = kwargs["user_payload"]["items"]
        calls.append(len(items))
        if len(calls) == 1:
            raise LlmRuntimeError("invalid json", code="llm_json_invalid", status_code=502)
        return {
            "items": [
                {
                    "parent_id": item["parent_id"],
                    "segments": [
                        {
                            "end_cue_id": item["source_cues"][0]["cue_id"],
                            "display_text": "第一段完整内容",
                            "tts_text": "第一段完整内容。",
                        },
                        {
                            "end_cue_id": item["source_cues"][1]["cue_id"],
                            "display_text": "接着是第二段完整内容",
                            "tts_text": "接着是第二段完整内容。",
                        },
                    ],
                }
                for item in items
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    replacements = localization._refine_candidate_batch(
        [(index, candidates[index], timed[index]) for index in range(2)],
        cue_by_id={cue.cue_id: cue for cue in cues},
        word_by_id={},
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
        request_state={"started_at": localization.time.perf_counter(), "requests": 0},
    )

    assert calls == [2, 1, 1]
    assert sorted(replacements) == [0, 1]
    assert all(len(items) == 2 for items in replacements.values())


def test_fit_candidate_segments_allows_a_final_targeted_refinement_round(monkeypatch):
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=4000,
                en_subtitle_text="First complete idea",
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                start_ms=4100,
                end_ms=9000,
                en_subtitle_text="Second complete idea",
            ),
        ]
    )
    candidate = {
        "id": "localized_0001",
        "source_cue_ids": ["cue_0001", "cue_0002"],
        "source_word_ids": [],
        "source_text": "First complete idea Second complete idea",
        "display_text": "这是第一段完整内容接下来是第二段完整内容需要按阅读时间拆开显示",
        "tts_text": "这是第一段完整内容。接下来是第二段完整内容，需要按阅读时间拆开显示。",
        "adaptation_note": "保留完整语义",
        "quality_flags": [],
    }
    calls = 0

    def complete_json(**_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            return {
                "items": [
                    {
                        "parent_id": "candidate_0000",
                        "segments": [
                            {
                                "end_cue_id": "cue_0002",
                                "display_text": candidate["display_text"],
                                "tts_text": candidate["tts_text"],
                            }
                        ],
                    }
                ]
            }
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_cue_id": "cue_0001",
                            "display_text": "这是第一段完整内容",
                            "tts_text": "这是第一段完整内容。",
                        },
                        {
                            "end_cue_id": "cue_0002",
                            "display_text": "接下来是第二段完整内容",
                            "tts_text": "接下来是第二段完整内容。",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        [candidate],
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert calls == 3
    assert [item["source_cue_ids"] for item in fitted] == [["cue_0001"], ["cue_0002"]]
    assert all(
        not localization._candidate_exceeds_budget(item) for item in localization._time_candidates(fitted, draft)
    )


def test_fit_candidate_segments_keeps_each_batched_parent_in_its_own_word_scope(monkeypatch):
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_01",
            text=text,
            start_ms=(index - 1) * 1000,
            end_ms=index * 1000,
        )
        for index, text in enumerate(["One", "shared", "cue", "then", "another"], start=1)
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=3000,
                en_subtitle_text="One shared cue",
                source_word_ids=["word_0001", "word_0002", "word_0003"],
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                start_ms=3000,
                end_ms=5000,
                en_subtitle_text="then another",
                source_word_ids=["word_0004", "word_0005"],
            ),
        ],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )
    candidates = [
        {
            "id": "localized_0001",
            "source_cue_ids": ["cue_0001"],
            "source_word_ids": ["word_0001"],
            "source_text": "One",
            "display_text": "这条字幕很长需要返修但只对应第一个源词不能拿到同一源句后面的词",
            "tts_text": "这条字幕很长，需要返修，但只对应第一个源词，不能拿到同一源句后面的词。",
            "adaptation_note": "拆分同一源句",
            "quality_flags": [],
        },
        {
            "id": "localized_0002",
            "source_cue_ids": ["cue_0001", "cue_0002"],
            "source_word_ids": ["word_0002", "word_0003", "word_0004", "word_0005"],
            "source_text": "shared cue then another",
            "display_text": "这一条合并了前一句剩余部分和后一句也需要继续严格按照各自准确词域重新拆开显示",
            "tts_text": "这一条合并了前一句剩余部分和后一句，也需要继续严格按照各自准确词域重新拆开显示。",
            "adaptation_note": "跨句合并",
            "quality_flags": [],
        },
    ]

    def complete_json(**kwargs):
        payload_items = kwargs["user_payload"]["items"]
        assert [
            [[word["word_id"] for word in cue["words"]] for cue in item["source_cues"]] for item in payload_items
        ] == [
            [["word_0001"]],
            [["word_0002", "word_0003"], ["word_0004", "word_0005"]],
        ]
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_word_id": "word_0001",
                            "display_text": "第一词",
                            "tts_text": "第一词。",
                        }
                    ],
                },
                {
                    "parent_id": "candidate_0001",
                    "segments": [
                        {
                            "end_word_id": "word_0003",
                            "display_text": "前句剩余",
                            "tts_text": "前句剩余。",
                        },
                        {
                            "end_word_id": "word_0005",
                            "display_text": "后一句",
                            "tts_text": "后一句。",
                        },
                    ],
                },
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        candidates,
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert [word_id for item in fitted for word_id in item["source_word_ids"]] == [
        "word_0001",
        "word_0002",
        "word_0003",
        "word_0004",
        "word_0005",
    ]
    assert [item["source_cue_ids"] for item in fitted] == [["cue_0001"], ["cue_0001"], ["cue_0002"]]


def test_fit_candidate_segments_falls_back_to_cue_boundaries_for_mixed_word_coverage(monkeypatch):
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_0001",
            segment_id="segment_01",
            text="First",
            start_ms=0,
            end_ms=3900,
        )
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=4000,
                en_subtitle_text="First",
                source_word_ids=["word_0001"],
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                start_ms=4100,
                end_ms=9000,
                en_subtitle_text="Second complete idea",
                source_word_ids=[],
            ),
        ],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )
    candidate = {
        "id": "localized_0001",
        "source_cue_ids": ["cue_0001", "cue_0002"],
        "source_word_ids": ["word_0001"],
        "source_text": "First Second complete idea",
        "display_text": "这是第一段完整内容接着是第二段完整内容",
        "tts_text": "这是第一段完整内容，接着是第二段完整内容。",
        "adaptation_note": "保留完整语义",
        "quality_flags": [],
    }

    def complete_json(**kwargs):
        item = kwargs["user_payload"]["items"][0]
        assert item["boundary_mode"] == "cue"
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_cue_id": "cue_0001",
                            "display_text": "这是第一段完整内容",
                            "tts_text": "这是第一段完整内容。",
                        },
                        {
                            "end_cue_id": "cue_0002",
                            "display_text": "接着是第二段完整内容",
                            "tts_text": "接着是第二段完整内容。",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        [candidate],
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert [item["source_cue_ids"] for item in fitted] == [["cue_0001"], ["cue_0002"]]
    assert [item["source_word_ids"] for item in fitted] == [["word_0001"], []]


def test_fit_candidate_segments_retries_whole_batch_when_llm_duplicates_boundaries(monkeypatch):
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_01",
            text=text,
            start_ms=(index - 1) * 1000,
            end_ms=index * 1000,
        )
        for index, text in enumerate(["First", "idea", "then", "second", "idea"], start=1)
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=5000,
                en_subtitle_text="First idea then second idea",
                source_word_ids=[word.word_id for word in words],
            )
        ],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )
    candidate = {
        "id": "localized_0001",
        "source_cue_ids": ["cue_0001"],
        "source_word_ids": [word.word_id for word in words],
        "source_text": "First idea then second idea",
        "display_text": "这是第一段非常非常长的完整内容然后还有第二段同样需要拆开的完整内容而且不能省略",
        "tts_text": "这是第一段非常非常长的完整内容，然后还有第二段同样需要拆开的完整内容，而且不能省略。",
        "adaptation_note": "保持两层语义",
        "quality_flags": [],
    }
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["user_payload"]["validation_retry"] is (calls == 2)
        first_boundary = "word_0002" if calls == 2 else "word_0005"
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_word_id": first_boundary,
                            "display_text": "这是第一段完整内容",
                            "tts_text": "这是第一段完整内容。",
                        },
                        {
                            "end_word_id": "word_0005",
                            "display_text": "然后是第二段完整内容",
                            "tts_text": "然后是第二段完整内容。",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        [candidate],
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert calls == 2
    assert [word_id for item in fitted for word_id in item["source_word_ids"]] == [word.word_id for word in words]


def test_fit_candidate_segments_retries_one_parent_when_llm_drops_a_number(monkeypatch):
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_01",
            text=text,
            start_ms=(index - 1) * 2100,
            end_ms=index * 2100,
        )
        for index, text in enumerate(["Seedance", "2.0", "in", "4K"], start=1)
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=8400,
                en_subtitle_text="Seedance 2.0 in 4K",
                source_word_ids=[word.word_id for word in words],
            )
        ],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )
    candidate = {
        "id": "localized_0001",
        "source_cue_ids": ["cue_0001"],
        "source_word_ids": [word.word_id for word in words],
        "source_text": "Seedance 2.0 in 4K",
        "display_text": "使用Seedance 2.0生成4K画面",
        "tts_text": "使用Seedance 2.0生成4K画面。",
        "adaptation_note": "保留版本和清晰度",
        "quality_flags": [],
    }
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["user_payload"]["validation_retry"] is (calls == 2)
        second_text = "生成画面" if calls == 1 else "生成4K画面"
        return {
            "items": [
                {
                    "parent_id": "candidate_0000",
                    "segments": [
                        {
                            "end_word_id": "word_0002",
                            "display_text": "使用Seedance 2.0",
                            "tts_text": "使用Seedance 2.0。",
                        },
                        {
                            "end_word_id": "word_0004",
                            "display_text": second_text,
                            "tts_text": f"{second_text}。",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    fitted = localization._fit_candidate_segments(
        [candidate],
        draft,
        context={},
        profile_id="llm_default",
        source_language="en",
        target_language="zh-Hans",
        is_cancelled=None,
    )

    assert calls == 2
    assert [item["display_text"] for item in fitted] == ["使用 Seedance 2.0", "生成 4K 画面"]


def test_localization_pipeline_honors_cancellation_before_llm(monkeypatch):
    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: pytest.fail("cancelled localization must not call the model"),
    )

    with pytest.raises(AppException) as exc_info:
        localization.generate_localization_draft(_draft(), is_cancelled=lambda: True)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_OPERATION_CANCELLED"


def test_research_step_result_reports_actual_usage():
    research = {
        "status": "completed",
        "reason": "只查证会影响理解的问题",
        "questions": [
            {
                "question_id": "research_01",
                "query": "Seedance official product name",
                "reason": "确认产品名称",
                "target_terms": ["Seedance"],
                "sources": [
                    {
                        "title": "官方产品页",
                        "url": "https://example.com/seedance",
                        "snippet": "Seedance 2.0",
                        "provider": "test",
                    }
                ],
            },
            {
                "question_id": "research_02",
                "query": "background only",
                "reason": "了解背景",
                "target_terms": [],
                "sources": [
                    {
                        "title": "背景资料",
                        "url": "https://example.com/background",
                        "snippet": "background",
                        "provider": "test",
                    }
                ],
            },
        ],
    }
    localized = [
        {
            "display_text": "这是 Seedance 2.0",
            "research_usage": [{"question_id": "research_01", "effect": "确认产品名应写作 Seedance 2.0"}],
        }
    ]

    result = localization._research_step_result(research, localized)

    metrics = {item["label"]: item["value"] for item in result["metrics"]}
    assert metrics["实际影响字幕"] == "1"
    first, second = result["sections"][0]["items"]
    assert "实际影响了 1 段字幕" in first["text"]
    assert first["facts"][2] == {"label": "采用结果", "value": "已用于 1 段字幕"}
    assert second["facts"][2] == {"label": "采用结果", "value": "仅参考，未直接采用"}


def test_localized_segment_keeps_only_declared_research_usage():
    cue = VideoLocalizationCue(
        cue_id="cue_0001",
        start_ms=0,
        end_ms=1200,
        en_subtitle_text="Product introduction",
    )

    parsed = localization._validate_localized_segments(
        [
            {
                "source_cue_ids": ["cue_0001"],
                "source_word_ids": [],
                "display_text": "产品介绍",
                "tts_text": "产品介绍。",
                "research_usage": [
                    {"question_id": "research_01", "effect": "确认产品采用官方中文名称"},
                    {"question_id": "research_unknown", "effect": "不应保留"},
                ],
            }
        ],
        [cue],
        {"cue_0001"},
        set(),
        {"cue_0001": cue},
        {},
        allowed_research_ids={"research_01"},
    )

    assert parsed[0]["research_usage"] == [{"question_id": "research_01", "effect": "确认产品采用官方中文名称"}]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("而且我还能用它做更多事：", "而且我还能用它做更多事。"),
        ("我的脸、走路时的动作、手上的戒指，", "我的脸、走路时的动作、手上的戒指。"),
        ("离谱吧？", "离谱吧？"),
        ("他说完了：‘可以，’", "他说完了：‘可以。’"),
    ],
)
def test_normalize_tts_text_replaces_dangling_terminal_separators(source, expected):
    assert localization._normalize_tts_text(source) == expected


def test_normalize_display_text_spaces_latin_terms_without_splitting_plain_number_units():
    assert localization._normalize_display_text("用Acme 3.1做成4K版本 1992年发布") == (
        "用 Acme 3.1 做成 4K 版本 1992年发布"
    )
