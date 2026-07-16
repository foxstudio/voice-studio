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


def test_localization_batches_balance_context_size_without_tiny_requests():
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

    assert [(start, len(batch)) for start, batch in batches] == [(0, 35), (35, 35)]
    assert all(
        sum(len(cue.source_word_ids) for cue in batch) <= localization.LOCALIZATION_BATCH_MAX_WORDS
        for _, batch in batches
    )


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
    context = {"speakers": [{"speaker_id": "speaker_01"}]}
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

    assert any("不能单独用‘干净’" in hint for hint in visual)
    assert any("体现不了" in hint for hint in visual)
    assert any("工具或 AI" in hint for hint in tool_pronoun)


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

    assert mixed_language["visible_chars"] == 13
    assert mixed_language["reading_units"] == 6
    assert mixed_language["cps"] == 4.48
    assert mixed_language["violations"] == []
    assert chinese_only["visible_chars"] == 13
    assert chinese_only["reading_units"] == 13
    assert chinese_only["cps"] == 9.7
    assert chinese_only["violations"] == ["阅读速度超过每秒9字，需要在不丢信息的前提下精简表达"]


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
            {
                "segments": [
                    {
                        "source_cue_ids": ["cue_0001", "cue_0002"],
                        "source_word_ids": [],
                        "display_text": "1992 年，这彻底改变了创作者。",
                        "tts_text": "1992 年，这彻底改变了创作者。",
                        "adaptation_note": "合并为一个完整语义",
                    }
                ]
            },
            {
                "checked_ids": ["localized_0001"],
                "changes": [
                    {
                        "id": "localized_0001",
                        "display_text": "1992 年 这彻底改变了创作者",
                        "tts_text": "1992 年，这彻底改变了创作者。",
                        "reason": "语义和口吻准确",
                    }
                ]
            },
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
    assert quality_metrics["计划批次"] == "1"
    assert quality_metrics["模型请求"] == "1"
    assert quality_metrics["失败拆分"] == "0"
    assert [phase for phase, _items in previews] == ["localized_draft", "localized_timing", "localized_review"]
    assert progress[-1][1] == "本土化字幕初稿已生成，正在保存"
    localize_rules = next(payload["rules"] for payload in payloads if payload["task"].endswith(":localize"))
    assert "speaker_voice" in localize_rules
    assert "discourse_markers" in localize_rules
    assert "cultural_function" in localize_rules


def test_localization_pipeline_refits_a_review_change_that_breaks_reading_budget(monkeypatch):
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
            return [
                {
                    "source_cue_ids": ["cue_0001"],
                    "source_word_ids": [],
                    "display_text": "看效果",
                    "tts_text": "看效果。",
                }
            ]
        if task.endswith(":quality-review"):
            return {
                "checked_ids": ["localized_0001"],
                "changes": [
                    {
                        "id": "localized_0001",
                        "display_text": "接下来我将向你详细展示这个最终生成出来的效果",
                        "tts_text": "接下来，我将向你详细展示这个最终生成出来的效果。",
                        "reason": "补充表达",
                    }
                ],
            }
        if task.endswith(":fit-segments"):
            return {
                "items": [
                    {
                        "parent_id": "candidate_0000",
                        "segments": [
                            {
                                "end_cue_id": "cue_0001",
                                "display_text": "接下来看最终效果",
                                "tts_text": "接下来看最终效果。",
                                "adaptation_note": "压缩为自然口语",
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(task)

    monkeypatch.setattr(
        localization.llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(profile_id="llm_default", model_id="deepseek-chat"),
    )
    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)

    run = localization.generate_localization_draft(draft)

    assert run.draft.localized_subtitles[0].text == "接下来看最终效果"
    assert sum(task.endswith(":fit-segments") for task in tasks) == 1
    metrics = {
        item["label"]: item["value"]
        for item in run.summary["task_step_results"]["post_review_constraints"]["metrics"]
    }
    assert metrics["二次返修"] == "1"
    assert metrics["剩余超限"] == "0"


def test_localization_pipeline_repairs_changed_numbers_once(monkeypatch):
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
            {
                "segments": [
                    {
                        "source_cue_ids": ["cue_0001", "cue_0002"],
                        "source_word_ids": [],
                        "display_text": "这彻底改变了创作者",
                        "tts_text": "这彻底改变了创作者。",
                    }
                ]
            },
            {
                "segments": [
                    {
                        "source_cue_ids": ["cue_0001", "cue_0002"],
                        "source_word_ids": [],
                        "display_text": "1992 年 这彻底改变了创作者",
                        "tts_text": "1992 年，这彻底改变了创作者。",
                    }
                ]
            },
            {
                "items": [
                    {
                        "id": "localized_0001",
                        "approved": True,
                        "display_text": "1992 年 这彻底改变了创作者",
                        "tts_text": "1992 年，这彻底改变了创作者。",
                        "reason": "数字和含义一致",
                    }
                ]
            },
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
    assert any(task.endswith(":repair-numbers") for task in tasks)


def test_localization_pipeline_rejects_numbers_when_repair_still_changes_them(monkeypatch):
    draft = _draft().model_copy(update={"cues": [_draft().cues[0]]})
    bad = {
        "segments": [
            {
                "source_cue_ids": ["cue_0001"],
                "source_word_ids": [],
                "display_text": "这彻底改变了创作者",
                "tts_text": "这彻底改变了创作者。",
            }
        ]
    }
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
            bad,
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


def test_localization_pipeline_splits_only_a_truncated_or_incomplete_batch(monkeypatch):
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
    monkeypatch.setattr(localization, "LOCALIZATION_BATCH_MAX_CUES", 4)
    monkeypatch.setattr(localization, "LOCALIZATION_BATCH_MAX_WORDS", 10_000)
    monkeypatch.setattr(localization, "LOCALIZATION_BATCH_MAX_SOURCE_CHARS", 10_000)
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
            cue_ids = [item["cue_id"] for item in payload["source_cues"]]
            localize_calls.append(cue_ids)
            if len(cue_ids) == 4:
                raise LlmRuntimeError("truncated", code="llm_output_truncated", status_code=502)
            source_cues = payload["source_cues"][:1] if cue_ids == ["cue_0001", "cue_0002"] else payload["source_cues"]
            return [
                {
                    "source_cue_ids": [item["cue_id"]],
                    "source_word_ids": [],
                    "display_text": f"译文 {'甲乙丙丁'[index]}",
                    "tts_text": f"译文 {'甲乙丙丁'[index]}。",
                }
                for index, item in enumerate(source_cues)
            ]
        if task.endswith(":quality-review"):
            item_ids = [item["id"] for item in payload["items"]]
            quality_review_calls.append(item_ids)
            if len(item_ids) == 4:
                raise LlmRuntimeError("timeout", code="llm_timeout", status_code=504)
            return [
                {
                    "id": item["id"],
                    "approved": True,
                    "display_text": item["display_text"],
                    "tts_text": item["tts_text"],
                    "reason": "通过",
                }
                for item in payload["items"]
            ]
        raise AssertionError(task)

    monkeypatch.setattr(localization.llm_runtime, "complete_json", complete_json)
    progress: list[tuple[float, str]] = []

    run = localization.generate_localization_draft(
        draft,
        on_progress=lambda value, stage: progress.append((value, stage)),
    )

    assert localize_calls == [
        ["cue_0001", "cue_0002", "cue_0003", "cue_0004"],
        ["cue_0001", "cue_0002"],
        ["cue_0001"],
        ["cue_0002"],
        ["cue_0003", "cue_0004"],
    ]
    assert len(run.draft.localized_subtitles) == 4
    assert any("内容较长，正在拆分" in stage for _value, stage in progress)
    assert [len(item_ids) for item_ids in quality_review_calls] == [4, 2, 2]
    quality_metrics = {
        item["label"]: item["value"] for item in run.summary["task_step_results"]["quality_review"]["metrics"]
    }
    assert quality_metrics["模型请求"] == "3"
    assert quality_metrics["失败拆分"] == "1"


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
        assert current["violations"] == ["时长超过7秒，需要按完整语义拆分"]
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

    assert calls == [12, 12, 9]
    assert len(fitted) == 66
    assert diagnostics["request_count"] == 3
    assert len(diagnostics["rounds"]) == 1
    assert diagnostics["rounds"][0] | {"duration_ms": 0} == {
        "round": 1,
        "problem_count": 33,
        "batch_count": 3,
        "duration_ms": 0,
    }
    assert len(previews) == 3
    assert progress[0][:3] == (0, 3, 1)


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
            start_ms=(index - 1) * 2000,
            end_ms=index * 2000,
        )
        for index, text in enumerate(["Seedance", "2.0", "in", "4K"], start=1)
    ]
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=8000,
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
    assert [item["display_text"] for item in fitted] == ["使用Seedance 2.0", "生成4K画面"]


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
