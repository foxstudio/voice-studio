from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_flow,
    operation_queue,
    review_decisions,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationGlossaryEntry,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime  # noqa: E402


def _segment(index: int, text: str) -> VideoLocalizationTranscriptSegment:
    return VideoLocalizationTranscriptSegment(
        segment_id=f"asr_{index:04d}",
        start_ms=(index - 1) * 1_000,
        end_ms=index * 1_000,
        raw_text=text,
    )


def _brief(segment_count: int) -> dict:
    return {
        "summary": "创作者讲解一套视频制作流程并比较结果。",
        "logic": ["先展示结果", "再解释制作流程"],
        "speaker_style": "第一人称教程讲解。",
        "entities": [
            {"name": "Seedance", "role": "可能的视频工具", "needs_research": True},
        ],
        "search_queries": [
            {
                "query": "Seedance 2.0 AI video tool",
                "category": "proper_noun",
                "reason": "核对产品名称。",
                "target_terms": ["Seedance 2.0", "Seedance"],
            }
        ],
        "sections": [
            {
                "id": "S1",
                "start_segment": 1,
                "end_segment": segment_count,
                "role": "介绍制作流程",
                "focus": ["核对产品名称", "检查操作顺序"],
            }
        ],
    }


@pytest.mark.parametrize("failed_step", ["visual_evidence", "normalize_entities"])
def test_degraded_review_retains_completed_steps_and_closes_failed_step(monkeypatch, failed_step):
    monkeypatch.setattr(asr_flow, "_resolve_profile", lambda _id: SimpleNamespace(profile_id="test"))
    events = []

    def fail_visual(*_args, **_kwargs):
        raise llm_runtime.LlmRuntimeError("Connection interrupted", code="codex_cli_execution_failed", status_code=502)

    def unused(*_args, **_kwargs):
        raise AssertionError("Downstream work must not run after the failure")

    source = [_segment(1, "A complete source sentence.")]
    result = asr_flow.review_transcript(
        source, language="en", profile_id="test",
        document_understanding_runner=lambda *_args, **_kwargs: _brief(1),
        visual_evidence_runner=(fail_visual if failed_step == "visual_evidence" else lambda *_args, **_kwargs: None),
        research_runner=lambda *_args, **_kwargs: VideoLocalizationResearchState(status="completed", reason="Evidence collected"),
        entity_normalization_runner=fail_visual, section_review_runner=unused,
        review_decisions_runner=unused, whole_recheck_runner=unused,
        on_report=lambda step, detail: events.append((step, detail)),
    )

    steps = result.report["task_step_results"]
    assert steps["understand_document"]["status"] == "success"
    assert steps[failed_step]["status"] == "warning"
    assert steps[failed_step]["error_detail"]["code"] == "codex_cli_execution_failed"
    assert not any(step["status"] == "running" for step in steps.values())
    assert "understand_document" in result.stage_timings
    assert result.review_meta["task_step_results"] == steps
    assert result.segments == source
    assert result.report["status"] == "degraded"
    assert [(step, detail["status"]) for step, detail in events if step == failed_step] == [
        (failed_step, "running"), (failed_step, "warning"),
    ]
    if failed_step == "normalize_entities":
        assert result.research.status == "completed"
        assert result.research.reason == "Evidence collected"


def test_section_item_normalizes_trailing_punctuation():
    item = asr_flow._section_item(
        {
            "id": "S1",
            "start_segment": 1,
            "end_segment": 2,
            "role": "开场",
            "focus": ["核对名称是否准确。"],
        }
    )

    assert item["text"] == "重点看：核对名称是否准确。"


def test_asr_flow_hides_a_warning_after_that_excerpt_has_been_corrected():
    segments = [_segment(1, "Use the verified product name here.")]
    segments[0] = segments[0].model_copy(update={"corrected_text": "Use the verified name here."})
    warnings = [
        asr_flow._uncertain_warning("asr_0001", "product name", "这个名称需要确认。"),
        asr_flow._uncertain_warning("asr_0001", "verified name", "请对照原音确认这一处。"),
    ]

    assert asr_flow._active_warnings(warnings, segments) == [warnings[1]]


def test_asr_flow_change_item_plainly_shows_before_after_and_reason():
    item = asr_flow._change_item(
        {
            "segment_id": "asr_0001",
            "before": "Use the wrong name.",
            "after": "Use the verified name.",
            "reason": "公开资料和上下文都指向这个写法。",
        }
    )

    assert item == {
        "title": "已修正一处听写",
        "text": "公开资料和上下文都指向这个写法。",
        "before": "Use the wrong name.",
        "after": "Use the verified name.",
        "before_label": "修改前",
        "after_label": "修改后",
        "facts": [],
        "links": [],
        "tone": "positive",
    }


def test_asr_flow_does_not_treat_repeated_conflicting_name_as_proof():
    segments = [
        _segment(1, "Seedance changes the background."),
        _segment(2, "Seedance relights the subject."),
        _segment(3, "C-ends creates the final clip."),
    ]

    result, changes, warnings = asr_flow._apply_decisions(
        segments,
        [
            {
                "segment_id": "asr_0001",
                "excerpt": "Seedance",
                "accept": True,
                "replacement": "C-ends",
                "reason": "统一工具名称。",
                "confidence": 0.98,
                "evidence_source_ids": [],
            }
        ],
        evidence=[],
    )

    assert result[0].corrected_text is None
    assert changes == []
    assert warnings[0]["message"] == (
        "这个名称没有查到可靠资料，已保留当前文本并继续；建议结合原音复听。"
    )


def test_asr_flow_only_falls_back_to_large_windows_after_explicit_length_error(monkeypatch):
    segments = [_segment(index, f"Segment {index} text") for index in range(1, 5)]
    calls = []

    def complete(prompt, payload, **_kwargs):
        calls.append((prompt, payload))
        if prompt.startswith("Read the complete ASR transcript"):
            raise llm_runtime.LlmRuntimeError(
                "input too long",
                code="llm_context_too_long",
                status_code=400,
            )
        if prompt.startswith("The full transcript request was too large"):
            assert payload["fallback_reason"] == "input too long"
            return _brief(len(segments))
        if prompt.startswith("Merge these ordered window briefs"):
            return _brief(len(segments))
        raise AssertionError(prompt)

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert result["summary"].startswith("创作者讲解")
    assert len(calls) == 3
    assert calls[0][1]["document"][-1]["segment_id"] == "asr_0004"
    assert "reader-facing metadata" in calls[1][0]
    assert "Simplified Chinese" in calls[1][0]
    assert "reader-facing metadata" in calls[2][0]
    assert "Simplified Chinese" in calls[2][0]
    assert calls[1][1]["output"]["summary"] == "简短内容概述"
    assert calls[1][1]["output"]["speaker_style"] == "讲话方式概述"


def test_document_understanding_adds_bounded_visual_check_for_unplanned_internal_title(
    monkeypatch,
):
    segments = [
        _segment(1, "The creator introduces Seedance."),
        _segment(2, "I drop my clip into Cloud."),
        _segment(3, "The interface writes the prompt."),
        _segment(4, "Then the creator shows the result."),
    ]

    def complete(prompt, _payload, **_kwargs):
        assert prompt.startswith("Read the complete ASR transcript")
        brief = _brief(len(segments))
        brief["visual_questions"] = []
        return brief

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert any(
        item["name"] == "Cloud"
        for item in result["entities"]
    )
    assert any(
        item["target_terms"] == ["Cloud"]
        for item in result["search_queries"]
    )
    question = next(
        item
        for item in result["visual_questions"]
        if "Cloud" in item["question"]
    )
    assert question["start_segment"] == 2
    assert question["end_segment"] == 2
    assert question["kind"] == "visible_text"
    assert len(result["visual_questions"]) <= 12


def test_document_understanding_routes_mixed_language_film_dialogue_to_visible_subtitles(
    monkeypatch,
):
    segments = [
        _segment(1, "The four friends enter the underworld."),
        _segment(
            2,
            "終わったぜ。最後に本気で楽しんだの、何百年前だよ。",
        ),
        _segment(3, "Patience, my little friend."),
    ]

    def complete(prompt, _payload, **_kwargs):
        assert "fictional, stylized, constructed, or foreign-language dialogue" in prompt
        brief = _brief(len(segments))
        brief.update(
            {
                "content_kind": "film_or_drama",
                "language_notes": [
                    "主体是英语电影对白，第 2 段出现日语或风格化语言。"
                ],
                "visual_questions": [],
            }
        )
        return brief

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="auto",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert result["content_kind"] == "film_or_drama"
    assert result["language_notes"]
    question = next(
        item
        for item in result["visual_questions"]
        if item["start_segment"] == 2
    )
    assert question["kind"] == "visible_text"
    assert "字幕" in question["question"]
    assert "不要根据音频翻译" in question["question"]


def test_asr_flow_entity_change_requires_cited_source_to_name_replacement():
    segments = [_segment(1, "Run the clip in Cineon 2.0.")]
    evidence = [
        {
            "source_id": "seedance-source",
            "title": "Seedance 2.0",
            "snippet": "Seedance 2.0 is an AI video generation model.",
        }
    ]

    rejected, rejected_changes, rejected_warnings = asr_flow._apply_decisions(
        segments,
        [
            {
                "segment_id": "asr_0001",
                "excerpt": "Cineon",
                "accept": True,
                "replacement": "CineSense",
                "reason": "Use the product name from the document brief.",
                "confidence": 0.98,
                "evidence_source_ids": ["seedance-source"],
            }
        ],
        evidence=evidence,
    )

    assert rejected[0].corrected_text is None
    assert rejected_changes == []
    assert rejected_warnings[0]["message"] == "引用资料没有出现建议的新名称，已保留原文。"

    accepted, accepted_changes, accepted_warnings = asr_flow._apply_decisions(
        segments,
        [
            {
                "segment_id": "asr_0001",
                "excerpt": "Cineon",
                "accept": True,
                "replacement": "Seedance",
                "reason": "The same-role product source names Seedance.",
                "confidence": 0.98,
                "evidence_source_ids": ["seedance-source"],
            }
        ],
        evidence=evidence,
    )

    assert accepted[0].corrected_text == "Run the clip in Seedance 2.0."
    assert accepted_changes[0]["evidence_source_ids"] == ["seedance-source"]
    assert accepted_warnings == []


def test_asr_flow_does_not_overwrite_a_confirmed_change_in_a_later_round():
    segments = [_segment(1, "Run the clip in Seedance 2.0 quicklyy.")]
    segments[0] = segments[0].model_copy(
        update={"corrected_text": "Run the clip in Seedance 2.0 quicklyy."}
    )
    locked_changes = [
        {
            "segment_id": "asr_0001",
            "before": "Run the clip in Cineon 2.0 quicklyy.",
            "after": "Run the clip in Seedance 2.0 quicklyy.",
            "reason": "公开资料已确认规范名称。",
            "confidence": 0.98,
        }
    ]
    evidence = [
        {
            "source_id": "cineon-source",
            "title": "Cineon 2.0",
            "snippet": "Cineon 2.0 is an AI video generation model.",
        }
    ]

    protected, protected_changes, protected_warnings = asr_flow._apply_decisions(
        segments,
        [
            {
                "segment_id": "asr_0001",
                "excerpt": "Seedance",
                "accept": True,
                "replacement": "Cineon",
                "reason": "Use the product name from the new source.",
                "confidence": 0.98,
                "evidence_source_ids": ["cineon-source"],
            }
        ],
        evidence=evidence,
        locked_changes=locked_changes,
    )

    assert protected[0].corrected_text == "Run the clip in Seedance 2.0 quicklyy."
    assert protected_changes == []
    assert protected_warnings[0]["message"] == "这一处已经在前一轮确认修改，本轮不再覆盖。"

    corrected, changes, warnings = asr_flow._apply_decisions(
        segments,
        [
            {
                "segment_id": "asr_0001",
                "excerpt": "quicklyy",
                "accept": True,
                "replacement": "quickly",
                "reason": "Correct a separate heard-word error.",
                "confidence": 0.98,
                "evidence_source_ids": [],
            }
        ],
        evidence=evidence,
        locked_changes=locked_changes,
    )

    assert corrected[0].corrected_text == "Run the clip in Seedance 2.0 quickly."
    assert len(changes) == 1
    assert warnings == []


def test_asr_flow_replans_one_giant_section_for_a_long_document(monkeypatch):
    segments = [_segment(index, f"Segment {index}") for index in range(1, 101)]
    calls = []

    def complete(prompt, payload, **_kwargs):
        calls.append((prompt, payload))
        if prompt.startswith("Read the complete ASR transcript"):
            brief = _brief(len(segments))
            brief["sections"] = [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": len(segments),
                    "role": "全文",
                    "focus": ["全部内容"],
                }
            ]
            return brief
        if prompt.startswith("Plan meaningful continuous review sections"):
            return {
                "sections": [
                    {"id": "S1", "start_segment": 1, "end_segment": 30, "role": "开场", "focus": ["背景"]},
                    {"id": "S2", "start_segment": 31, "end_segment": 70, "role": "主体", "focus": ["步骤"]},
                    {"id": "S3", "start_segment": 71, "end_segment": 100, "role": "收尾", "focus": ["结论"]},
                ]
            }
        raise AssertionError(prompt)

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert len(result["sections"]) == 3
    assert result["sections"][1]["role"] == "主体"
    assert calls[1][1]["preferred_section_count"] == 5


def test_asr_flow_replans_eighty_segments_into_multiple_review_sections(monkeypatch):
    segments = [_segment(index, f"Segment {index}") for index in range(1, 81)]
    calls = []

    def complete(prompt, payload, **_kwargs):
        calls.append((prompt, payload))
        if prompt.startswith("Read the complete ASR transcript"):
            return _brief(len(segments))
        if prompt.startswith("Plan meaningful continuous review sections"):
            return {
                "sections": [
                    {"id": "S1", "start_segment": 1, "end_segment": 20, "role": "开场", "focus": ["核对人物"]},
                    {"id": "S2", "start_segment": 21, "end_segment": 40, "role": "观点一", "focus": ["核对论据"]},
                    {"id": "S3", "start_segment": 41, "end_segment": 60, "role": "观点二", "focus": ["核对术语"]},
                    {"id": "S4", "start_segment": 61, "end_segment": 80, "role": "结尾", "focus": ["核对结论"]},
                ]
            }
        raise AssertionError(prompt)

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert len(result["sections"]) == 4
    assert calls[1][1]["preferred_section_count"] == 4


def test_asr_flow_retries_an_empty_document_brief_once(monkeypatch):
    segments = [_segment(index, f"Segment {index}") for index in range(1, 5)]
    calls = []

    def complete(prompt, payload, **_kwargs):
        calls.append((prompt, payload))
        if len(calls) == 1:
            return {
                "summary": "",
                "logic": [],
                "speaker_style": "",
                "entities": [],
                "search_queries": [],
                "sections": [],
            }
        return _brief(len(segments))

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert result["summary"].startswith("创作者讲解")
    assert len(calls) == 2
    assert calls[1][1]["attempt"] == 2


def test_asr_flow_retries_reader_metadata_that_is_not_chinese(monkeypatch):
    segments = [_segment(index, f"Segment {index}") for index in range(1, 5)]
    calls = []

    def complete(_prompt, _payload, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {
                "summary": "An interview about AI chips.",
                "logic": ["Introduce the topic", "Explain the risks"],
                "speaker_style": "Two-person interview.",
                "entities": [],
                "search_queries": [],
                "sections": [
                    {
                        "id": "S1",
                        "start_segment": 1,
                        "end_segment": 4,
                        "role": "Full interview",
                        "focus": ["Check names"],
                    }
                ],
            }
        return _brief(len(segments))

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert result["summary"].startswith("创作者讲解")
    assert len(calls) == 2


def test_asr_flow_retries_when_document_brief_lacks_style_or_distinct_section_focus(monkeypatch):
    segments = [_segment(index, f"Segment {index}") for index in range(1, 5)]
    calls = []

    def complete(prompt, payload, **_kwargs):
        calls.append((prompt, payload))
        brief = _brief(len(segments))
        if len(calls) == 1:
            brief["speaker_style"] = ""
            brief["sections"] = [
                {"id": "S1", "start_segment": 1, "end_segment": 2, "role": "主体", "focus": ["检查内容"]},
                {"id": "S2", "start_segment": 3, "end_segment": 4, "role": "主体", "focus": ["检查内容"]},
            ]
        return brief

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    result = asr_flow._understand_document(
        segments,
        language="en",
        scene_context="",
        profile_id="work",
        is_cancelled=None,
    )

    assert result["speaker_style"] == "第一人称教程讲解。"
    assert len(calls) == 2


def test_asr_flow_keeps_string_section_focus_as_one_instruction():
    sections = asr_flow._normalize_sections(
        [
            {
                "id": "S1",
                "start_segment": 1,
                "end_segment": 2,
                "role": "开场",
                "focus": "核对主题和首次出现的专名",
            }
        ],
        segment_count=2,
        allow_empty=True,
    )

    assert sections[0]["focus"] == ["核对主题和首次出现的专名"]


def test_asr_flow_limits_final_user_warnings_but_keeps_a_plain_summary():
    warnings = [asr_flow._uncertain_warning(f"asr_{index:04d}", "name", "名称待确认") for index in range(1, 13)]

    limited = asr_flow._limit_user_warnings(warnings)

    assert len(limited) == 9
    assert limited[-1]["message"] == "另外还有 4 处同类问题，字幕没有改，请结合原音确认。"


def test_asr_flow_masks_unverified_names_in_document_context_but_keeps_candidates():
    brief = asr_flow._normalize_brief(
        {
            "summary": "Use CineSense 2.0 on Higgsfield to create effects.",
            "logic": ["Introduce CineSense 2.0", "Show the workflow"],
            "speaker_style": "tutorial",
            "entities": [
                {"name": "CineSense 2.0", "role": "candidate tool", "needs_research": True},
                {"name": "Higgsfield", "role": "confirmed platform", "needs_research": False},
            ],
            "sections": [{"id": "S1", "start_segment": 1, "end_segment": 2, "role": "intro", "focus": ["names"]}],
        },
        segment_count=2,
    )

    assert brief["summary"] == "Use [名称待核实] on Higgsfield to create effects."
    assert brief["logic"][0] == "Introduce [名称待核实]"
    assert brief["entities"][0]["name"] == "CineSense 2.0"


def test_asr_flow_caps_document_research_terms_before_planned_query_validation():
    target_terms = [
        "CineSense 2.0",
        "Cineon 2.0",
        "Seedent",
        "Seadance",
        "C-ends",
        "Ceren's",
        "C-dance",
        "Hidens",
        "sedans",
        "Cines 2.0",
    ]
    brief = asr_flow._normalize_brief(
        {
            "summary": "A creator demonstrates one AI video model.",
            "logic": ["Compare several transcript spellings."],
            "speaker_style": "tutorial",
            "entities": [],
            "search_queries": [
                {
                    "query": "Seedance 2.0 AI video model",
                    "category": "proper_noun",
                    "reason": "Resolve competing transcript spellings.",
                    "target_terms": target_terms,
                }
            ],
            "sections": [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": 2,
                    "role": "intro",
                    "focus": ["names"],
                }
            ],
        },
        segment_count=2,
    )

    assert brief["search_queries"][0]["target_terms"] == target_terms[:8]
    asr_flow.web_research.PlannedQuery.model_validate(
        brief["search_queries"][0]
    )


def test_asr_flow_resolves_candidate_only_when_cited_evidence_contains_canonical_name(monkeypatch):
    evidence = [
        {
            "source_id": "source_seedance",
            "title": "Text-to-video model",
            "snippet": "Seedance 2.0 from ByteDance is an AI video generator.",
        }
    ]

    monkeypatch.setattr(
        asr_flow,
        "_complete_json_stable",
        lambda *_args, **_kwargs: {
            "resolutions": [
                {
                    "canonical_name": "Seedance 2.0",
                    "variants": ["CineSense 2", "C-ends", "Seedent"],
                    "role": "AI video generator",
                    "confidence": 0.96,
                    "evidence_source_ids": ["source_seedance"],
                },
                {
                    "canonical_name": "CineSense 2.0",
                    "variants": ["Cineon 2"],
                    "role": "AI video generator",
                    "confidence": 0.99,
                    "evidence_source_ids": ["source_seedance"],
                },
            ]
        },
    )

    resolved = asr_flow._resolve_researched_entities(
        {"summary": "AI video demo", "entities": _brief(1)["entities"]},
        segments=[_segment(1, "Run the clip in Seedance 2.0.")],
        evidence=evidence,
        profile_id="work",
    )

    assert [item["canonical_name"] for item in resolved] == ["Seedance 2.0"]


def test_asr_flow_delegates_reasoning_depth_to_saved_profile(
    monkeypatch,
):
    calls = []

    def complete(*_args, **kwargs):
        calls.append(kwargs)
        return {"resolutions": []}

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)

    asr_flow._resolve_researched_entities(
        {
            "summary": "AI video demo",
            "entities": [
                {
                    "name": "CineSense 2.0",
                    "role": "待核对工具名称",
                    "needs_research": True,
                }
            ],
        },
        segments=[_segment(1, "Run the clip in CineSense 2.0.")],
        evidence=[
            {
                "source_id": "source_seedance",
                "title": "Seedance 2.0",
                "snippet": "Seedance 2.0 is an AI video generator.",
            }
        ],
        profile_id="work",
    )

    assert "reasoning_effort" not in calls[0]


def test_asr_flow_gives_entity_resolver_local_sentence_context():
    contexts = asr_flow._candidate_entity_contexts(
        [{"name": "Cineon 2", "role": "candidate tool"}],
        [
            _segment(1, "This is the generated result."),
            _segment(2, "Run the same footage in Cineon 2."),
            _segment(3, "The tool preserves camera motion."),
        ],
    )

    assert contexts[0]["occurrences"][0] == {
        "segment_id": "asr_0002",
        "before": "This is the generated result.",
        "text": "Run the same footage in Cineon 2.",
        "after": "The tool preserves camera motion.",
    }


def test_asr_flow_maps_different_sounding_asr_variants_after_name_is_verified(monkeypatch):
    monkeypatch.setattr(
        asr_flow,
        "_complete_json_stable",
        lambda *_args, **_kwargs: {
            "mappings": [
                {
                    "canonical_name": "Seedance",
                    "variants": ["CineSense 2", "C-ends", "Hidens", "C-dance", "Unrelated Platform"],
                    "reason": "same core tool in the workflow",
                }
            ]
        },
    )

    mapped = asr_flow._map_resolved_entity_variants(
        {"summary": "one core video tool", "logic": ["show", "explain", "use"]},
        resolutions=[
            {
                "canonical_name": "Seedance",
                "variants": ["Seedent"],
                "role": "AI video tool",
                "confidence": 0.9,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        candidate_contexts=[
            {"name": "CineSense 2", "occurrences": []},
            {"name": "C-ends", "occurrences": []},
            {"name": "Hidens", "occurrences": []},
        ],
        document=[{"segment_id": "asr_0001", "text": "Run the clip in C-dance."}],
        profile_id="work",
    )

    assert mapped[0]["variants"] == ["Seedent", "CineSense 2", "C-ends", "Hidens", "C-dance"]


def test_asr_flow_locally_maps_unique_confusable_candidate_when_model_omits_it(
    monkeypatch,
):
    monkeypatch.setattr(
        asr_flow,
        "_complete_json_stable",
        lambda *_args, **_kwargs: {
            "mappings": [
                {
                    "canonical_name": "Seedance 2.0",
                    "variants": ["Seedance"],
                    "reason": "same core tool",
                }
            ]
        },
    )

    mapped = asr_flow._map_resolved_entity_variants(
        {"summary": "one continuous Seedance workflow", "logic": []},
        resolutions=[
            {
                "canonical_name": "Seedance 2.0",
                "variants": [],
                "confidence": 0.97,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        candidate_contexts=[
            {
                "name": "CineSense 2",
                "occurrences": [
                    {
                        "segment_id": "asr_0001",
                        "text": "The same shot mixed with CineSense 2.",
                    }
                ],
            }
        ],
        document=[
            {"segment_id": "asr_0001", "text": "The same shot mixed with CineSense 2."},
            {"segment_id": "asr_0012", "text": "Everything was created using Seedance 2."},
            {"segment_id": "asr_0047", "text": "Seedance only swaps the world behind me."},
            {"segment_id": "asr_0057", "text": "Seedance relights me into any scene."},
        ],
        profile_id="work",
    )

    assert mapped[0]["variants"] == ["Seedance", "CineSense 2"]


def test_asr_flow_merges_singleton_confusable_wrapper_name_into_dominant_entity(
    monkeypatch,
):
    calls = 0

    def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "resolutions": [
                    {
                        "canonical_name": "Seedance 2.0",
                        "variants": ["Seedance", "CineSense 2"],
                        "role": "主要视频生成模型",
                        "confidence": 0.96,
                        "evidence_source_ids": ["source_seedance"],
                    },
                    {
                        "canonical_name": "C Dance AI",
                        "variants": ["C-dance"],
                        "role": "视频生成网站",
                        "confidence": 0.88,
                        "evidence_source_ids": ["source_cdance"],
                    },
                ]
            }
        return {
            "mappings": [
                {
                    "canonical_name": "Seedance 2.0",
                    "variants": ["Seedance", "CineSense 2", "C-dance"],
                    "reason": "the same model and workflow",
                }
            ]
        }

    monkeypatch.setattr(asr_flow, "_complete_json_stable", complete)
    segments = [
        _segment(1, "This tutorial uses Seedance 2.0."),
        _segment(2, "Seedance preserves the subject."),
        _segment(3, "Run another shot in Seedance."),
        _segment(4, "I drop that same clip into C-dance with a prompt and run it."),
    ]

    resolved = asr_flow._resolve_researched_entities(
        {
            "summary": "A continuous Seedance workflow",
            "logic": ["Introduce the model", "Run multiple clips in the same model"],
            "entities": [
                {
                    "name": "Seedance 2.0",
                    "role": "主要视频模型",
                    "needs_research": True,
                },
                {
                    "name": "C-dance",
                    "role": "同一流程中的疑似名称",
                    "needs_research": True,
                },
            ],
        },
        segments=segments,
        evidence=[
            {
                "source_id": "source_seedance",
                "title": "Seedance 2.0",
                "snippet": "Seedance 2.0 is an AI video generation model.",
            },
            {
                "source_id": "source_cdance",
                "title": "C Dance AI",
                "snippet": "C Dance AI is a website powered by Seedance 2.0.",
            },
        ],
        profile_id="work",
    )

    assert calls == 2
    assert [item["canonical_name"] for item in resolved] == ["Seedance 2.0"]
    assert "C-dance" in resolved[0]["variants"]


def test_asr_flow_counts_versionless_mentions_for_dominant_entity():
    segments = [
        _segment(1, "This tutorial uses Seedance 2."),
        _segment(2, "Seedance preserves the subject."),
        _segment(3, "Run another shot in Seedance."),
        _segment(4, "I drop that same clip into C-dance with a prompt and run it."),
    ]

    filtered = asr_flow._drop_confusable_singleton_resolutions(
        [
            {
                "canonical_name": "Seedance 2.0",
                "variants": ["CineSense 2"],
                "confidence": 0.97,
                "evidence_source_ids": ["source_seedance"],
            },
            {
                "canonical_name": "C Dance ai",
                "variants": ["C-dance"],
                "confidence": 0.90,
                "evidence_source_ids": ["source_cdance"],
            },
        ],
        segments=segments,
        evidence=[],
    )

    assert [item["canonical_name"] for item in filtered] == ["Seedance 2.0"]


def test_asr_flow_projects_clause_level_name_mappings_back_to_candidate_spans(
    monkeypatch,
):
    monkeypatch.setattr(
        asr_flow,
        "_complete_json_stable",
        lambda *_args, **_kwargs: {
            "mappings": [
                {
                    "canonical_name": "Seedance",
                    "variants": [
                        "C-ends should only replace the environment around me.",
                        "Hidens actually understands physics.",
                    ],
                    "reason": "same tool",
                }
            ]
        },
    )

    mapped = asr_flow._map_resolved_entity_variants(
        {"summary": "video effects", "logic": []},
        resolutions=[
            {
                "canonical_name": "Seedance",
                "variants": [],
                "confidence": 0.95,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        candidate_contexts=[
            {"name": "C-ends", "occurrences": []},
            {"name": "Hidens", "occurrences": []},
        ],
        document=[
            {
                "segment_id": "asr_0001",
                "text": (
                    "C-ends should only replace the environment around me. "
                    "Hidens actually understands physics."
                ),
            }
        ],
        profile_id="work",
    )

    assert mapped[0]["variants"] == ["C-ends", "Hidens"]


def test_asr_flow_applies_verified_name_variants_without_changing_version_numbers():
    segments = [
        _segment(1, "Created with CineSense 2 in 4K."),
        _segment(2, "Run the prompt in C-ends."),
        _segment(3, "Drop the clip into C- dance."),
    ]
    evidence = [
        {
            "source_id": "source_seedance",
            "title": "Text-to-video model",
            "snippet": "Seedance 2.0 from ByteDance is an AI video generator.",
        }
    ]

    result, changes, warnings = asr_flow._apply_resolved_entity_variants(
        segments,
        resolutions=[
            {
                "canonical_name": "Seedance 2.0",
                "variants": ["CineSense 2", "C-ends", "C-dance"],
                "confidence": 0.95,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        evidence=evidence,
    )

    assert result[0].corrected_text == "Created with Seedance 2 in 4K."
    assert result[1].corrected_text == "Run the prompt in Seedance."
    assert result[2].corrected_text == "Drop the clip into Seedance."
    assert len(changes) == 3
    assert warnings == []


def test_asr_flow_applies_verified_versioned_name_split_across_adjacent_segments():
    segments = [
        _segment(1, "This is the same shot mixed with CineSense 2."),
        _segment(2, "0 in 4K."),
        _segment(3, "Everything was created using Cineon 2."),
        _segment(4, "0 inside Higgsfield."),
    ]
    evidence = [
        {
            "source_id": "source_seedance",
            "title": "Seedance 2.0",
            "snippet": "Seedance 2.0 from ByteDance is an AI video model.",
        }
    ]

    result, changes, warnings = asr_flow._apply_resolved_entity_variants(
        segments,
        resolutions=[
            {
                "canonical_name": "Seedance 2.0",
                "variants": ["CineSense 2.0", "Cineon 2.0"],
                "confidence": 0.99,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        evidence=evidence,
    )

    assert result[0].corrected_text == (
        "This is the same shot mixed with Seedance 2."
    )
    assert result[1].corrected_text is None
    assert result[2].corrected_text == (
        "Everything was created using Seedance 2."
    )
    assert result[3].corrected_text is None
    assert [item["segment_id"] for item in changes] == [
        "asr_0001",
        "asr_0003",
    ]
    assert warnings == []


def test_asr_flow_does_not_record_noop_name_replacements():
    result, changes, warnings = asr_flow._apply_resolved_entity_variants(
        [_segment(1, "Created with Seedance 2.0.")],
        resolutions=[
            {
                "canonical_name": "Seedance",
                "variants": ["Seedance 2.0"],
                "confidence": 0.95,
                "evidence_source_ids": ["source_seedance"],
            }
        ],
        evidence=[
            {
                "source_id": "source_seedance",
                "title": "Seedance 2.0",
                "snippet": "Seedance 2.0 is a video model.",
            }
        ],
    )

    assert result[0].corrected_text is None
    assert changes == []
    assert warnings == []


def test_asr_flow_matches_short_name_variants_as_complete_words_only():
    segments = [
        _segment(1, "Yeah, Ed."),
        _segment(2, "AI-related stocks closed lower."),
        _segment(3, "The model is priced for the market."),
    ]
    evidence = [
        {
            "source_id": "source_ed_ludlow",
            "title": "Ed Ludlow",
            "snippet": "Ed Ludlow hosts Bloomberg Technology.",
        }
    ]

    result, changes, warnings = asr_flow._apply_resolved_entity_variants(
        segments,
        resolutions=[
            {
                "canonical_name": "Ed Ludlow",
                "variants": ["Ed"],
                "confidence": 0.9,
                "evidence_source_ids": ["source_ed_ludlow"],
            }
        ],
        evidence=evidence,
    )

    assert result[0].corrected_text == "Yeah, Ed Ludlow."
    assert result[1].corrected_text is None
    assert result[2].corrected_text is None
    assert [item["segment_id"] for item in changes] == ["asr_0001"]
    assert warnings == []
