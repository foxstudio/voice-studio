"""Small, provider-neutral search client for research evidence."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from app.errors import AppException
from app.schemas.voice_studio import WebSearchSettings


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_QUERY_LENGTH = 240
USER_AGENT = "VoiceStudio/1.2 web-research"
HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    retrieved_at: str | None = None


def search(settings: WebSearchSettings, query: str, *, api_key: str | None = None) -> list[SearchResult]:
    normalized = " ".join(query.split())[:MAX_QUERY_LENGTH]
    if not normalized:
        return []
    if settings.provider == "wikipedia":
        return _wikipedia(normalized, settings.max_results_per_query)
    if settings.provider == "tavily":
        if not api_key:
            raise AppException(400, "WEB_SEARCH_KEY_MISSING", "Tavily 尚未配置 API Key")
        return _tavily(normalized, api_key, settings.max_results_per_query)
    if settings.provider == "searxng":
        if not settings.base_url:
            raise AppException(400, "WEB_SEARCH_URL_MISSING", "请填写 SearXNG 服务地址")
        return _searxng(normalized, settings.base_url, settings.max_results_per_query)
    raise AppException(400, "WEB_SEARCH_PROVIDER_UNSUPPORTED", "不支持这个搜索服务")


def search_general_web(query: str, *, limit: int) -> list[SearchResult]:
    """Use a bounded, keyless web search for unresolved proper nouns."""

    normalized = " ".join(query.split())[:MAX_QUERY_LENGTH]
    if not normalized or limit <= 0:
        return []
    params = urllib.parse.urlencode({"q": normalized})
    raw = _request_text(f"https://lite.duckduckgo.com/lite/?{params}")
    if (
        "anomaly-modal" in raw
        or 'name="image-check_' in raw
    ):
        raise AppException(
            502,
            "WEB_SEARCH_CHALLENGE",
            "通用网页搜索触发了访问验证，请稍后重试或更换搜索服务。",
        )
    parser = _DuckDuckGoResultsParser(limit=limit)
    parser.feed(raw)
    parser.close()
    return parser.results


def _wikipedia(query: str, limit: int) -> list[SearchResult]:
    language = "zh" if re.search(r"[\u3400-\u9fff]", query) else "en"
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "utf8": 1,
            "format": "json",
            "origin": "*",
        }
    )
    payload = _request_json(f"https://{language}.wikipedia.org/w/api.php?{params}")
    rows = ((payload.get("query") or {}).get("search") or []) if isinstance(payload, dict) else []
    results = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        url_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
        results.append(
            SearchResult(
                title=title[:300],
                url=f"https://{language}.wikipedia.org/wiki/{url_title}",
                snippet=_clean_snippet(row.get("snippet")),
            )
        )
    return results


def _tavily(query: str, api_key: str, limit: int) -> list[SearchResult]:
    payload = _request_json(
        "https://api.tavily.com/search",
        body={
            "query": query,
            "search_depth": "basic",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return _common_results(payload.get("results") if isinstance(payload, dict) else None, limit)


def _searxng(query: str, base_url: str, limit: int) -> list[SearchResult]:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AppException(400, "WEB_SEARCH_URL_INVALID", "SearXNG 地址格式不正确")
    params = urllib.parse.urlencode({"q": query, "format": "json", "safesearch": 1})
    payload = _request_json(f"{base_url.rstrip('/')}/search?{params}")
    return _common_results(payload.get("results") if isinstance(payload, dict) else None, limit)


def _common_results(rows: Any, limit: int) -> list[SearchResult]:
    results = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        title = str(row.get("title") or parsed.hostname).strip()
        results.append(
            SearchResult(
                title=title[:300],
                url=url[:2000],
                snippet=_clean_snippet(row.get("content") or row.get("snippet")),
            )
        )
        if len(results) >= limit:
            break
    return results


def _request_json(url: str, *, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = "WEB_SEARCH_AUTH_FAILED" if exc.code in {401, 403} else "WEB_SEARCH_HTTP_ERROR"
        message = "搜索服务鉴权失败，请检查 API Key" if code == "WEB_SEARCH_AUTH_FAILED" else f"搜索服务返回 HTTP {exc.code}"
        raise AppException(502, code, message) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AppException(502, "WEB_SEARCH_UNAVAILABLE", "暂时无法连接搜索服务") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AppException(502, "WEB_SEARCH_RESPONSE_TOO_LARGE", "搜索服务返回内容过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppException(502, "WEB_SEARCH_RESPONSE_INVALID", "搜索服务没有返回有效 JSON") from exc


def _request_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AppException(502, "WEB_SEARCH_HTTP_ERROR", f"搜索服务返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AppException(502, "WEB_SEARCH_UNAVAILABLE", "暂时无法连接搜索服务") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AppException(502, "WEB_SEARCH_RESPONSE_TOO_LARGE", "搜索服务返回内容过大")
    return raw.decode("utf-8", errors="replace")


class _DuckDuckGoResultsParser(HTMLParser):
    def __init__(self, *, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[SearchResult] = []
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._in_snippet = False
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag in {"td", "div"} and "result-snippet" in classes and self.results:
            self._in_snippet = True
            self._snippet_parts = []
            return
        if tag != "a" or len(self.results) >= self.limit:
            return
        if not classes.intersection({"result__a", "result-link"}):
            return
        self._href = values.get("href") or ""
        self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "div"} and self._in_snippet:
            self._in_snippet = False
            snippet = _clean_snippet(" ".join(self._snippet_parts))
            self._snippet_parts = []
            if snippet and self.results:
                result = self.results[-1]
                self.results[-1] = SearchResult(result.title, result.url, snippet)
            return
        if tag != "a" or self._href is None:
            return
        title = " ".join("".join(self._title_parts).split())
        url = _duckduckgo_result_url(self._href)
        self._href = None
        self._title_parts = []
        if not title or not url or any(item.url == url for item in self.results):
            return
        self.results.append(SearchResult(title=title[:300], url=url[:2000], snippet=""))


def _duckduckgo_result_url(value: str) -> str | None:
    url = value.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if not target:
            return None
        url = urllib.parse.unquote(target)
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
            return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return url


def _clean_snippet(value: Any) -> str:
    text = HTML_TAG.sub(" ", str(value or ""))
    return " ".join(text.split())[:1200]
