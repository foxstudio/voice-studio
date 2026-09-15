from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import web_research  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationResearchQuery,
    VideoLocalizationResearchSource,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
)
from app.models.schemas import LlmProviderProfile, LlmProviderListResponse, WebSearchSettings  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.services import web_search  # noqa: E402


def _segments():
    return [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001", start_ms=0, end_ms=1000, raw_text="Made with Seed ants 2.0"
        )
    ]


def _configure_research_planner(monkeypatch, *, max_queries: int = 3) -> None:
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=max_queries)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)


def test_research_planner_submits_the_complete_transcript_by_default(monkeypatch):
    _configure_research_planner(monkeypatch)
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{index:04d}",
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
            raw_text=f"segment-{index} " + character * 7000,
        )
        for index, character in enumerate(("A", "B", "C"), start=1)
    ]
    calls = []

    def complete_json(**kwargs):
        calls.append(kwargs)
        return {"needs_research": False, "reason": "no external facts needed", "queries": []}

    monkeypatch.setattr(web_research.llm_runtime, "complete_json", complete_json)

    result = web_research.research_transcript(segments, language="en")

    expected = " ".join(segment.raw_text for segment in segments)
    assert result.status == "not_needed"
    assert len(expected) > 18000
    assert len(calls) == 1
    assert calls[0]["user_payload"]["transcript"] == expected
    assert "fallback_section" not in calls[0]["user_payload"]


@pytest.mark.parametrize("error_code", ["llm_context_too_long", "llm_output_truncated"])
def test_research_planner_uses_non_overlapping_cores_with_overlap_context_after_explicit_length_error(
    monkeypatch,
    error_code,
):
    _configure_research_planner(monkeypatch)
    monkeypatch.setattr(web_research, "FALLBACK_CORE_MAX_SEGMENTS", 2, raising=False)
    monkeypatch.setattr(web_research, "FALLBACK_CORE_MAX_CHARS", 10000, raising=False)
    monkeypatch.setattr(web_research, "FALLBACK_OVERLAP_SEGMENTS", 1, raising=False)
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{index:04d}",
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
            raw_text=f"ordinary transcript segment {index}",
        )
        for index in range(1, 6)
    ]
    calls = []

    def complete_json(**kwargs):
        calls.append(kwargs)
        payload = kwargs["user_payload"]
        if "fallback_section" not in payload:
            raise web_research.llm_runtime.LlmRuntimeError(
                "request is too large",
                code=error_code,
                status_code=400,
            )
        return {
            "needs_research": True,
            "reason": "verify the same uncertain name",
            "queries": [
                {
                    "query": "Shared Name",
                    "category": "proper_noun",
                    "reason": "verify spelling",
                    "target_terms": ["Shared Name"],
                }
            ],
        }

    monkeypatch.setattr(web_research.llm_runtime, "complete_json", complete_json)
    search_calls = []

    def search(_settings, query, *, api_key=None):
        search_calls.append(query)
        return [web_search.SearchResult("Shared Name", "https://example.com/shared", "Verified name")]

    monkeypatch.setattr(web_research.web_search, "search", search)

    result = web_research.research_transcript(segments, language="en")

    fallback_calls = [call for call in calls if "fallback_section" in call["user_payload"]]
    assert result.status == "completed"
    assert len(calls) == 4
    assert len(fallback_calls) == 3
    assert [item["index"] for call in fallback_calls for item in call["user_payload"]["core_transcript"]] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert [[item["index"] for item in call["user_payload"]["context_before"]] for call in fallback_calls] == [
        [],
        [2],
        [4],
    ]
    assert [[item["index"] for item in call["user_payload"]["context_after"]] for call in fallback_calls] == [
        [3],
        [5],
        [],
    ]
    assert all("Analyze only core_transcript" in call["system_prompt"] for call in fallback_calls)
    assert [query.query for query in result.queries] == ["Shared Name"]
    assert search_calls == ["Shared Name"]


def test_research_plans_once_searches_and_reuses_cache(tmp_path: Path, monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=2, max_results_per_query=3)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        web_research.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "needs_research": True,
            "reason": "产品名可能误听",
            "queries": [
                {
                    "query": "Seedance 2.0 AI video",
                    "category": "proper_noun",
                    "reason": "核对产品名",
                    "target_terms": ["Seedance"],
                }
            ],
        },
    )
    calls = []

    def fake_search(_settings, query, *, api_key=None):
        calls.append(query)
        return [web_search.SearchResult("Seedance 2.0", "https://example.com/seedance", "AI video model")]

    monkeypatch.setattr(web_research.web_search, "search", fake_search)
    first = web_research.research_transcript(_segments(), language="en", cache_dir=tmp_path)
    second = web_research.research_transcript(_segments(), language="en", cache_dir=tmp_path)

    assert first.status == "completed"
    assert first.sources[0].url == "https://example.com/seedance"
    assert second.cache_hits == 1
    assert calls == ["Seedance 2.0 AI video"]
    assert web_research.evidence_payload(first)[0]["source_id"] == first.sources[0].source_id


def test_research_query_budget_comes_from_workflow_not_legacy_settings(monkeypatch):
    _configure_research_planner(monkeypatch, max_queries=1)
    calls: list[str] = []

    def fake_search(_settings, query, *, api_key=None):
        calls.append(query)
        return [
            web_search.SearchResult(
                query,
                f"https://example.com/{len(calls)}",
                f"Evidence about {query}",
            )
        ]

    monkeypatch.setattr(web_research.web_search, "search", fake_search)
    result = web_research.research_transcript(
        _segments(),
        language="en",
        planned_queries=[
            web_research.PlannedQuery(
                query="Seedance 2.0",
                category="proper_noun",
                target_terms=["Seedance"],
            ),
            web_research.PlannedQuery(
                query="ByteDance AI video",
                category="background",
                target_terms=["ByteDance"],
            ),
        ],
        augment_planned_queries=False,
        max_queries=2,
    )

    assert result.status == "completed"
    assert calls == ["Seedance 2.0", "ByteDance AI video"]


def test_research_plan_retries_truncated_json_without_reasoning(monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=1)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    calls = []

    def complete_json(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("语言模型输出因长度限制而不完整")
        return {
            "needs_research": True,
            "reason": "核对产品名",
            "queries": [
                {
                    "query": "Seedance 2.0",
                    "category": "proper_noun",
                    "reason": "核对产品名",
                    "target_terms": ["Seedance 2.0", "Seedance"],
                }
            ],
        }

    monkeypatch.setattr(web_research.llm_runtime, "complete_json", complete_json)
    monkeypatch.setattr(
        web_research.web_search,
        "search",
        lambda *_args, **_kwargs: [
            web_search.SearchResult("Seedance 2.0", "https://example.com/seedance", "AI video model")
        ],
    )

    result = web_research.research_transcript(_segments(), language="en")

    assert result.status == "completed"
    assert len(calls) == 2
    assert all(call["disable_reasoning"] is True for call in calls)
    assert [call["user_payload"]["attempt"] for call in calls] == [1, 2]
    assert all("fallback_section" not in call["user_payload"] for call in calls)


def test_wikipedia_research_falls_back_to_general_web_for_unresolved_proper_name(tmp_path: Path, monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=1, max_results_per_query=3)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        web_research.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "needs_research": True,
            "reason": "核对产品名",
            "queries": [
                {
                    "query": "Seedance 2.0 ByteDance",
                    "category": "proper_noun",
                    "reason": "核对产品名",
                    "target_terms": ["Seedance 2.0", "Seedance"],
                }
            ],
        },
    )
    monkeypatch.setattr(web_research.web_search, "search", lambda *_args, **_kwargs: [])
    fallback_calls = []

    def general_search(query, *, limit):
        fallback_calls.append((query, limit))
        return [
            web_search.SearchResult(
                "Seedance 2.0 - seed.bytedance.com",
                "https://seed.bytedance.com/en/seedance2_0",
                "ByteDance video generation model",
            )
        ]

    monkeypatch.setattr(web_research.web_search, "search_general_web", general_search)

    result = web_research.research_transcript(_segments(), language="en", cache_dir=tmp_path)

    assert result.status == "completed"
    assert result.sources[0].provider == "duckduckgo"
    assert result.sources[0].title == "Seedance 2.0 - seed.bytedance.com"
    assert fallback_calls == [("Seedance 2.0 ByteDance", 3)]


def test_short_exact_person_name_is_not_discarded_when_identity_context_matches():
    query = VideoLocalizationResearchQuery(
        query_id="query_01",
        query="Bloomberg host Ed AI interview",
        category="proper_noun",
        reason="核对主持人姓名。",
        target_terms=["Ed"],
    )

    assert web_research._provider_result_supports_query(
        query,
        "Ed Ludlow | Bloomberg Media Talent",
        "Ed Ludlow is a Bloomberg Television host covering technology.",
        provider="duckduckgo",
    )
    assert not web_research._provider_result_supports_query(
        query,
        "Ed Sheeran official website",
        "Music, tour dates, and album news.",
        provider="duckduckgo",
    )


def test_research_planner_treats_source_title_as_proper_name_evidence(monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=1)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    captured = {}

    def complete_json(**kwargs):
        captured.update(kwargs)
        return {"needs_research": False, "reason": "title resolves the likely product name", "queries": []}

    monkeypatch.setattr(web_research.llm_runtime, "complete_json", complete_json)

    result = web_research.research_transcript(
        _segments(),
        language="en",
        scene_context="源视频标题：seedance-speech-30s.mp4",
    )

    assert result.status == "not_needed"
    assert captured["user_payload"]["scene_context"] == "源视频标题：seedance-speech-30s.mp4"
    assert "source titles" in captured["system_prompt"]
    assert "ordinary wording" in captured["system_prompt"]
    assert "technology" not in captured["system_prompt"].casefold()


def test_title_conflict_candidates_require_scene_title_exact_result_and_shared_version():
    state = VideoLocalizationResearchState(
        status="completed",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="seedance",
                category="proper_noun",
                target_terms=["seedance"],
            ),
            VideoLocalizationResearchQuery(
                query_id="query_02",
                query="Cinebench 2.0",
                category="proper_noun",
                target_terms=["Cinebench 2.0", "Cinebench"],
            ),
        ],
        sources=[
            VideoLocalizationResearchSource(
                source_id="source_seedance",
                query_id="query_01",
                title="Seedance 2.0",
                url="https://example.com/seedance",
                snippet="AI video model",
                provider="wikipedia",
            ),
            VideoLocalizationResearchSource(
                source_id="source_cinema",
                query_id="query_02",
                title="Cinema 4D",
                url="https://example.com/cinema",
                snippet="Cinebench benchmark",
                provider="wikipedia",
            ),
        ],
    )

    conflicts = web_research.title_conflict_candidates(
        state,
        scene_context="源视频标题：seedance-speech-30s.mp4",
        transcript="The same shot mixed with Cinebench 2. 0 in 4K.",
    )

    assert conflicts == [
        {
            "conflict_id": "title_conflict_01",
            "source_text": "Cinebench",
            "corrected_source_text": "Seedance",
            "version": "2.0",
            "scene_term": "seedance",
            "confirmed_title": "Seedance 2.0",
            "evidence_source_ids": ["source_seedance"],
            "raw_term_exact_title_supported": False,
        }
    ]


def test_scene_title_query_replaces_redundant_second_raw_name_query():
    planned = [
        web_research.PlannedQuery(
            query="Cinebench 2.0",
            category="proper_noun",
            target_terms=["Cinebench", "2.0"],
        ),
        web_research.PlannedQuery(
            query="Cinebench 20",
            category="proper_noun",
            target_terms=["Cinebench", "20"],
        ),
    ]

    guarded = web_research._ensure_scene_title_query(
        planned,
        scene_context="源视频标题：seedance-speech-30s-720p.mp4",
        transcript="The shot was mixed with Cinebench 2. 0 in 4K. Insane, right?",
        limit=2,
    )

    assert [item.query for item in guarded] == ["Cinebench 2.0", "seedance"]
    assert guarded[1].target_terms == ["seedance"]


def test_scene_title_query_is_split_from_combined_llm_query():
    planned = [
        web_research.PlannedQuery(
            query="seedance CineDan 2.0",
            category="proper_noun",
            target_terms=["CineDan", "2.0", "seedance"],
        )
    ]

    guarded = web_research._ensure_scene_title_query(
        planned,
        scene_context="源视频标题：seedance-speech-30s-720p.mp4",
        transcript="The same shot was mixed with CineDan 2. 0 in 4K.",
        limit=3,
    )

    assert [item.query for item in guarded] == ["seedance CineDan 2.0", "seedance"]


def test_competing_transcript_name_gets_its_own_versioned_query():
    planned = [
        web_research.PlannedQuery(
            query="CineDan 2.0 AI tool",
            category="proper_noun",
            target_terms=["CineDan", "Cinean", "C-Deance", "Cines", "Seedance"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI", category="proper_noun", target_terms=["Higgsfield"]),
        web_research.PlannedQuery(query="Claude AI", category="proper_noun", target_terms=["Claude"]),
    ]

    guarded = web_research._ensure_competing_name_query(
        planned,
        transcript=("The same shot was mixed with CineDan 2. 0 in 4K. Seedance in 4K quality completely changed that."),
        limit=3,
    )

    assert [item.query for item in guarded] == [
        "CineDan 2.0 AI tool",
        "Seedance 2.0 AI tool",
        "Higgsfield AI",
    ]
    assert guarded[1].target_terms == ["Seedance 2.0", "Seedance"]


def test_competing_name_query_handles_single_integer_version():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2", "Cineon 2", "C-ends"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI", category="proper_noun", target_terms=["Higgsfield"]),
    ]

    guarded = web_research._ensure_competing_name_query(
        planned,
        transcript="CineSense 2 and Cineon 2 are inconsistent spellings.",
        limit=3,
    )

    assert guarded[1].query.startswith("Cineon 2")


def test_repeated_competing_name_query_ignores_single_integer_version_when_comparing():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2", "Cineon 2", "C-ends"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI", category="proper_noun", target_terms=["Higgsfield"]),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript="Seedance creates video. Later Seedance relights the scene.",
        limit=3,
    )

    assert guarded[1].query.startswith("Seedance")


def test_repeated_competing_name_uses_explicit_candidate_query_after_compound_query():
    planned = [
        web_research.PlannedQuery(
            query="Higgsfield Seedance 2.0 AI video effects",
            category="proper_noun",
            target_terms=[],
        ),
        web_research.PlannedQuery(
            query="CineSense 2.0 Higgsfield",
            category="proper_noun",
            target_terms=["CineSense", "C-ends", "C-dance", "Cines"],
        ),
        web_research.PlannedQuery(
            query="Cines 2.0",
            category="proper_noun",
            target_terms=["Cines 2.0", "Cines"],
        ),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript="Seedance changes the background. Later Seedance relights the subject.",
        limit=3,
    )

    assert guarded[1].query == "Seedance 2.0"


def test_repeated_competing_name_prefers_fuller_searchable_spelling_over_shorter_variant():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI", category="proper_noun", target_terms=["Higgsfield"]),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript=(
            "C-ends changes one scene. C-ends changes another scene. C-ends relights it. "
            "Seedance changes the background. Later Seedance preserves the camera move."
        ),
        limit=3,
    )

    assert guarded[1].query.startswith("Seedance")


def test_candidate_term_does_not_count_as_separately_searched_after_query_truncation():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2 AI video effects tool",
            category="proper_noun",
            target_terms=["CineSense 2", "Cineon 2"],
        ),
        web_research.PlannedQuery(
            query="Seedent AI video 4K",
            category="proper_noun",
            target_terms=["Seedent", "Seedance"],
        ),
        web_research.PlannedQuery(
            query="Higgsfield CineSense",
            category="proper_noun",
            target_terms=["Higgsfield", "CineSense 2"],
        ),
    ]
    competing = web_research._ensure_competing_name_query(
        planned,
        transcript="CineSense 2 and Cineon 2 appear once. Seedance works. Later Seedance relights it.",
        limit=3,
    )

    guarded = web_research._ensure_repeated_competing_name_query(
        competing,
        transcript="C-ends appears three times. C-ends again. C-ends again. Seedance works. Later Seedance relights it.",
        limit=3,
    )

    assert guarded[1].query.startswith("Seedance")


def test_repeated_candidate_does_not_displace_an_already_searched_repeated_variant():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2 AI video effects",
            category="proper_noun",
            target_terms=["CineSense 2"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI", category="proper_noun", target_terms=["Higgsfield"]),
        web_research.PlannedQuery(query="Seedance AI tool", category="proper_noun", target_terms=["Seedance"]),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript="C-ends appears often. C-ends again. C-ends again. Seedance works. Seedance relights it.",
        limit=3,
    )

    assert [item.query for item in guarded] == [
        "CineSense 2 AI video effects",
        "Higgsfield AI",
        "Seedance 2 AI tool",
    ]
    assert guarded[2].target_terms == ["Seedance 2", "Seedance"]


def test_llm_planned_repeated_candidate_beats_unplanned_similar_common_word():
    planned = [
        web_research.PlannedQuery(
            query="Cineon 2 AI video tool",
            category="proper_noun",
            target_terms=["Cineon 2", "CineSense 2"],
        ),
        web_research.PlannedQuery(
            query="CineSense 2 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2", "CineSense"],
        ),
        web_research.PlannedQuery(
            query="Seedance 4K AI effects",
            category="proper_noun",
            target_terms=["Seedance", "Seedent"],
        ),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript="Insane result. Insane again. Seedance changes the scene. Seedance relights it.",
        limit=3,
    )

    assert guarded[2].query == "Seedance 4K AI effects"


def test_repeated_competing_name_replaces_weak_single_occurrence_query():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2.0 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2.0", "Cineon 2.0", "Cines 2.0"],
        ),
        web_research.PlannedQuery(
            query="Cineon 2.0 AI video tool",
            category="proper_noun",
            target_terms=["Cineon 2.0", "Cineon"],
        ),
        web_research.PlannedQuery(
            query="Higgsfield AI video platform",
            category="proper_noun",
            target_terms=["Higgsfield"],
        ),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript=(
            "The shot used CineSense 2. 0. Later Seedance changed the background. "
            "Run it in Seedance. Seedance preserved the camera move. Cineon 2. 0 appeared once."
        ),
        limit=3,
    )

    assert [item.query for item in guarded] == [
        "CineSense 2.0 AI video tool",
        "Seedance 2.0 AI video tool",
        "Cineon 2.0 AI video tool",
    ]
    assert guarded[1].target_terms == ["Seedance 2.0", "Seedance"]


def test_competing_transcript_name_is_searched_separately_when_aggregate_query_mentions_it():
    planned = [
        web_research.PlannedQuery(
            query="CineDan 2.0 Seedance Higgsfield",
            category="proper_noun",
            target_terms=["CineDan 2.0", "Seedance", "Higgsfield", "Cinean 2.0", "C-Deance"],
        ),
        web_research.PlannedQuery(query="Claude AI video frames", category="proper_noun", target_terms=["Claude"]),
    ]

    guarded = web_research._ensure_competing_name_query(
        planned,
        transcript=("The same shot was mixed with CineDan 2.0 in 4K. Seedance in 4K quality completely changed that."),
        limit=3,
    )

    assert [item.query for item in guarded] == [
        "CineDan 2.0 Seedance Higgsfield",
        "Seedance 2.0",
        "Claude AI video frames",
    ]


def test_versioned_proper_name_result_requires_the_same_version():
    query = VideoLocalizationResearchQuery(
        query_id="query_01",
        query="CineDan 2.0 AI tool",
        category="proper_noun",
        target_terms=["CineDan", "Cines", "Seedance"],
    )

    assert not web_research._result_supports_query(
        query,
        "DJI",
        "Mavic 2 Pro and Mavic 3 Cine are camera drones.",
    )
    assert web_research._result_supports_query(
        query,
        "Seedance 2.0",
        "ByteDance AI video generation model.",
    )
    assert web_research._result_supports_query(
        query,
        "Sora (text-to-video model)",
        "Veo Dream Machine Seedance 2 . 0 LTX AI model.",
    )
    assert not web_research._provider_result_supports_query(
        query,
        "Sora (text-to-video model)",
        "Veo Dream Machine Seedance 2 . 0 LTX AI model.",
        provider="wikipedia",
    )
    assert web_research._provider_result_supports_query(
        query,
        "Seedance 2.0",
        "ByteDance AI video generation model.",
        provider="wikipedia",
    )


def test_repeated_competing_name_ignores_common_exclamation():
    planned = [
        web_research.PlannedQuery(
            query="CineSense 2.0 AI video tool",
            category="proper_noun",
            target_terms=["CineSense 2.0", "Seedance"],
        ),
        web_research.PlannedQuery(query="Higgsfield AI platform", category="proper_noun", target_terms=["Higgsfield"]),
    ]

    guarded = web_research._ensure_repeated_competing_name_query(
        planned,
        transcript="Insane, right? Seedance changed the shot. Insane result. Run it in Seedance.",
        limit=3,
    )

    assert all("Insane" not in item.query for item in guarded)


def test_proper_name_result_requires_identity_context_and_nonempty_summary():
    query = VideoLocalizationResearchQuery(
        query_id="query_01",
        query="Cineon 2.0 AI video tool",
        category="proper_noun",
        target_terms=["Cineon 2.0", "Cineon"],
    )

    assert not web_research._result_supports_query(
        query,
        "Cineon - 2.0 APK for Android Download",
        "",
    )
    assert not web_research._result_supports_query(
        query,
        "Cineon - 2.0 APK for Android Download",
        "Streaming application APK for Android phones.",
    )
    assert web_research._result_supports_query(
        query,
        "Cineon 2.0 AI video tool",
        "A video generation model for editing footage.",
    )


def test_title_conflict_uses_versioned_transcript_name_when_target_terms_are_split():
    state = VideoLocalizationResearchState(
        status="completed",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="seedance CineDan 2.0",
                category="proper_noun",
                target_terms=["CineDan", "2.0", "seedance"],
            ),
            VideoLocalizationResearchQuery(
                query_id="query_02",
                query="seedance",
                category="proper_noun",
                target_terms=["seedance"],
            ),
        ],
        sources=[
            VideoLocalizationResearchSource(
                source_id="source_seedance",
                query_id="query_02",
                title="Seedance 2.0",
                url="https://example.com/seedance",
                snippet="AI video model",
                provider="wikipedia",
            )
        ],
    )

    conflicts = web_research.title_conflict_candidates(
        state,
        scene_context="源视频标题：seedance-speech-30s.mp4",
        transcript="The same shot was mixed with CineDan 2. 0 in 4K.",
    )

    assert conflicts[0]["source_text"] == "CineDan"
    assert conflicts[0]["corrected_source_text"] == "Seedance"
    assert conflicts[0]["version"] == "2.0"


def test_research_skips_llm_when_disabled(monkeypatch):
    monkeypatch.setattr(
        web_research.settings_store,
        "web_search_settings",
        lambda: WebSearchSettings(enabled=False),
    )
    result = web_research.research_transcript(_segments(), language="en")
    assert result.status == "disabled"


def test_research_plan_normalizes_common_model_category_aliases():
    plan = web_research.ResearchPlan.model_validate(
        {
            "needs_research": True,
            "queries": [
                {
                    "query": "Seedance 2.0 official product",
                    "category": "product",
                    "reason": "verify product spelling",
                    "target_terms": ["Seedance"],
                }
            ],
        }
    )

    assert plan.queries[0].category == "proper_noun"


def test_research_zero_results_is_completed_without_evidence(monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=1)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        web_research.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "needs_research": True,
            "reason": "核对产品名",
            "queries": [{"query": "Unknown product", "category": "proper_noun"}],
        },
    )
    monkeypatch.setattr(web_research.web_search, "search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(web_research.web_search, "search_general_web", lambda *_args, **_kwargs: [])

    result = web_research.research_transcript(_segments(), language="en")

    assert result.status == "completed"
    assert result.sources == []
    assert "没有找到" in result.reason


def test_research_discards_unrelated_proper_name_results(monkeypatch):
    settings = WebSearchSettings(enabled=True, provider="wikipedia", max_queries=1)
    profile = LlmProviderProfile(
        profile_id="work", name="Work", base_url="https://example.com/v1", model_id="chat", enabled=True
    )
    monkeypatch.setattr(web_research.settings_store, "web_search_settings", lambda: settings)
    monkeypatch.setattr(web_research.settings_store, "web_search_api_key", lambda: None)
    monkeypatch.setattr(
        web_research.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id="work"),
    )
    monkeypatch.setattr(web_research.settings_store, "llm_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        web_research.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "needs_research": True,
            "reason": "核对产品名",
            "queries": [
                {
                    "query": "CineDan 2.0",
                    "category": "proper_noun",
                    "target_terms": ["CineDan 2.0"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        web_research.web_search,
        "search",
        lambda *_args, **_kwargs: [
            web_search.SearchResult("Nicusor Dan", "https://example.com/person", "Romanian politician"),
            web_search.SearchResult("AlloCine", "https://example.com/cinema", "French movie database"),
        ],
    )
    monkeypatch.setattr(web_research.web_search, "search_general_web", lambda *_args, **_kwargs: [])

    result = web_research.research_transcript(_segments(), language="en")

    assert result.status == "completed"
    assert result.sources == []
    assert "没有找到" in result.reason


def test_evidence_payload_filters_unrelated_legacy_sources():
    state = VideoLocalizationResearchState(
        status="completed",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="CineDan 2.0",
                category="proper_noun",
                target_terms=["CineDan 2.0"],
            )
        ],
        sources=[
            VideoLocalizationResearchSource(
                source_id="source_bad",
                query_id="query_01",
                title="Nicusor Dan",
                url="https://example.com/person",
                snippet="Romanian politician",
                provider="wikipedia",
            )
        ],
    )

    assert web_research.evidence_payload(state) == []


def test_cache_write_failure_does_not_fail_search(tmp_path: Path, monkeypatch):
    settings = WebSearchSettings(provider="wikipedia", max_results_per_query=3)
    monkeypatch.setattr(
        web_research.web_search,
        "search",
        lambda *_args, **_kwargs: [web_search.SearchResult("Seedance", "https://example.com", "AI model")],
    )

    def fail_cache_write(*_args, **_kwargs):
        raise OSError("read-only cache")

    monkeypatch.setattr(web_research, "_write_cache", fail_cache_write)

    results, cache_hit, error = web_research._search_cached(
        settings,
        "Seedance",
        api_key=None,
        cache_dir=tmp_path,
    )

    assert results[0].title == "Seedance"
    assert cache_hit is False
    assert error is None


def test_expired_cache_is_removed(tmp_path: Path):
    cache_path = tmp_path / "expired.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": web_research.CACHE_SCHEMA_VERSION,
                "created_at_epoch": time.time() - web_research.CACHE_TTL_SECONDS - 1,
                "results": [],
            }
        ),
        encoding="utf-8",
    )

    assert web_research._read_cache(cache_path) is None
    assert not cache_path.exists()


def test_sensitive_queries_are_not_sent():
    queries = [
        web_research.PlannedQuery(query="Seedance 2.0 background"),
        web_research.PlannedQuery(query="person@example.com project background"),
        web_research.PlannedQuery(query="API key abcdefghijklmnopqrstuvwxyz123456"),
    ]

    planned = web_research._normalize_queries(queries, 3)

    assert [item.query for item in planned] == ["Seedance 2.0 background"]


def test_transient_search_failure_is_retried_once(monkeypatch):
    settings = WebSearchSettings(provider="wikipedia", max_results_per_query=3)
    calls = []

    def search(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise AppException(502, "WEB_SEARCH_UNAVAILABLE", "暂时无法连接搜索服务")
        return [web_search.SearchResult("Seedance 2.0", "https://example.com/seedance", "AI video model")]

    monkeypatch.setattr(web_research.web_search, "search", search)
    monkeypatch.setattr(web_research.time, "sleep", lambda _seconds: None)

    results, cache_hit, error = web_research._search_cached(
        settings,
        "Seedance 2.0",
        api_key=None,
        cache_dir=None,
    )

    assert len(calls) == 2
    assert results[0].title == "Seedance 2.0"
    assert cache_hit is False
    assert error is None


def test_failed_query_is_rewritten_with_failure_context_and_actual_query_is_recorded(monkeypatch):
    _configure_research_planner(monkeypatch, max_queries=1)
    planned = [
        web_research.PlannedQuery(
            query="CineDan 2.0",
            category="proper_noun",
            reason="核对疑似误听的产品名称",
            target_terms=["CineDan 2.0", "Seedance"],
        )
    ]
    search_calls = []

    def search(_settings, query, *, api_key=None):
        search_calls.append(query)
        if query == "Seedance 2.0 ByteDance AI video model":
            return [
                web_search.SearchResult(
                    "Seedance 2.0",
                    "https://seed.bytedance.com/en/seedance2_0",
                    "ByteDance AI video generation model",
                )
            ]
        return [web_search.SearchResult("Nicusor Dan", "https://example.com/person", "Romanian politician")]

    monkeypatch.setattr(web_research.web_search, "search", search)
    monkeypatch.setattr(web_research.web_search, "search_general_web", lambda *_args, **_kwargs: [])
    rewrite_calls = []

    def complete_json(**kwargs):
        rewrite_calls.append(kwargs)
        return {"query": "Seedance 2.0 ByteDance AI video model"}

    monkeypatch.setattr(web_research.llm_runtime, "complete_json", complete_json)

    result = web_research.research_transcript(
        _segments(),
        language="en",
        scene_context="源视频标题：speech-30s.mp4；AI 视频生成演示",
        planned_queries=planned,
    )

    assert result.status == "completed"
    assert [item.query for item in result.queries] == ["Seedance 2.0 ByteDance AI video model"]
    assert [item.title for item in result.sources] == ["Seedance 2.0"]
    assert search_calls == ["CineDan 2.0", "Seedance 2.0 ByteDance AI video model"]
    assert len(rewrite_calls) == 1
    payload = rewrite_calls[0]["user_payload"]
    assert payload["original_query"] == "CineDan 2.0"
    assert payload["target_terms"] == ["CineDan 2.0", "Seedance"]
    assert payload["failure_reason"] == "搜索结果与目标名称不相关"
    assert payload["rejected_result_titles"] == ["Nicusor Dan"]
    assert "speech-30s.mp4" in payload["scene_context"]


def test_rewritten_query_still_discards_unrelated_results(monkeypatch):
    _configure_research_planner(monkeypatch, max_queries=1)
    monkeypatch.setattr(
        web_research.web_search,
        "search",
        lambda *_args, **_kwargs: [
            web_search.SearchResult("Nicusor Dan", "https://example.com/person", "Romanian politician")
        ],
    )
    monkeypatch.setattr(web_research.web_search, "search_general_web", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        web_research.llm_runtime,
        "complete_json",
        lambda **_kwargs: {"query": "Seedance 2.0 ByteDance AI video model"},
    )

    result = web_research.research_transcript(
        _segments(),
        language="en",
        planned_queries=[
            web_research.PlannedQuery(
                query="CineDan 2.0",
                category="proper_noun",
                target_terms=["CineDan 2.0", "Seedance"],
            )
        ],
    )

    assert result.status == "completed"
    assert result.sources == []
    assert "没有找到" in result.reason
