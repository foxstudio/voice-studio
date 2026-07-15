from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import web_research  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationTranscriptSegment  # noqa: E402
from app.models.schemas import LlmProviderProfile, LlmProviderListResponse, WebSearchSettings  # noqa: E402
from app.services import web_search  # noqa: E402


def _segments():
    return [VideoLocalizationTranscriptSegment(segment_id="asr_0001", start_ms=0, end_ms=1000, raw_text="Made with Seed ants 2.0")]


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


def test_research_skips_llm_when_disabled(monkeypatch):
    monkeypatch.setattr(
        web_research.settings_store,
        "web_search_settings",
        lambda: WebSearchSettings(enabled=False),
    )
    result = web_research.research_transcript(_segments(), language="en")
    assert result.status == "disabled"


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

    result = web_research.research_transcript(_segments(), language="en")

    assert result.status == "completed"
    assert result.sources == []
    assert "没有找到" in result.reason


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
