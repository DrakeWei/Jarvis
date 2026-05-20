from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.core.config import settings


class TavilySearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class TavilySearchItem:
    title: str
    url: str
    score: float
    snippet: str
    raw_excerpt: str


@dataclass(frozen=True)
class TavilySearchResponse:
    query: str
    searched_at: str
    evidence_quality: str
    results: list[TavilySearchItem]
    suggested_answer: str | None
    include_domains: list[str]
    retried_without_domains: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "query": self.query,
            "searched_at": self.searched_at,
            "evidence_quality": self.evidence_quality,
            "results": [
                {
                    "title": item.title,
                    "url": item.url,
                    "score": item.score,
                    "snippet": item.snippet,
                    "raw_excerpt": item.raw_excerpt,
                }
                for item in self.results
            ],
            "suggested_answer": self.suggested_answer,
            "include_domains": self.include_domains,
            "retried_without_domains": self.retried_without_domains,
        }


_DEFAULT_BASE_URL = "https://api.tavily.com/search"
_SNIPPET_LIMIT = 320
_RAW_EXCERPT_LIMIT = 420
_MAX_RESULTS_CAP = 5
_TIME_RANGE_ALIASES = {
    "d": "day",
    "day": "day",
    "today": "day",
    "daily": "day",
    "24h": "day",
    "24hr": "day",
    "24hrs": "day",
    "w": "week",
    "week": "week",
    "weekly": "week",
    "this week": "week",
    "m": "month",
    "month": "month",
    "monthly": "month",
    "this month": "month",
    "y": "year",
    "year": "year",
    "yearly": "year",
    "this year": "year",
}
_NBA_HINT_PATTERNS = (
    "nba",
    "cavaliers",
    "knicks",
    "eastern conference finals",
    "东决",
    "骑士",
    "尼克斯",
)


def search_web(
    query: str,
    *,
    max_results: int | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    search_depth: str | None = None,
    time_range: str | None = None,
    include_raw_content: bool | None = None,
) -> TavilySearchResponse:
    normalized_query = " ".join(query.strip().split())
    if not normalized_query:
        raise TavilySearchError("web_search requires a non-empty query.")
    if not settings.jarvis_tavily_enabled:
        raise TavilySearchError("Web search is not enabled. Set JARVIS_TAVILY_ENABLED=1 to use Tavily search.")
    if not settings.jarvis_tavily_api_key:
        raise TavilySearchError("Web search is not configured. Set JARVIS_TAVILY_API_KEY before using web_search.")

    resolved_max_results = min(
        max(1, max_results if isinstance(max_results, int) and max_results > 0 else settings.jarvis_tavily_max_results_default),
        _MAX_RESULTS_CAP,
    )
    resolved_include_domains = _normalize_domains(include_domains) or _default_include_domains(normalized_query)
    resolved_exclude_domains = _normalize_domains(exclude_domains)
    resolved_search_depth = _normalize_search_depth(search_depth)
    resolved_include_raw = True if include_raw_content is None else bool(include_raw_content)

    response = _request_search(
        query=normalized_query,
        max_results=resolved_max_results,
        include_domains=resolved_include_domains,
        exclude_domains=resolved_exclude_domains,
        search_depth=resolved_search_depth,
        time_range=_normalize_time_range(time_range),
        include_raw_content=resolved_include_raw,
    )
    retried = False
    if resolved_include_domains and _should_retry_without_domains(response):
        response = _request_search(
            query=normalized_query,
            max_results=resolved_max_results,
            include_domains=[],
            exclude_domains=resolved_exclude_domains,
            search_depth=resolved_search_depth,
            time_range=_normalize_time_range(time_range),
            include_raw_content=resolved_include_raw,
        )
        retried = True
        resolved_include_domains = []

    results = _parse_results(response, limit=resolved_max_results)
    evidence_quality = _grade_evidence(results)
    return TavilySearchResponse(
        query=normalized_query,
        searched_at=datetime.now(timezone.utc).isoformat(),
        evidence_quality=evidence_quality,
        results=results,
        suggested_answer=_suggested_answer(results, evidence_quality),
        include_domains=resolved_include_domains,
        retried_without_domains=retried,
    )


def serialize_response(response: TavilySearchResponse) -> str:
    return json.dumps(response.to_payload(), ensure_ascii=True, indent=2)


def _request_search(
    *,
    query: str,
    max_results: int,
    include_domains: list[str],
    exclude_domains: list[str],
    search_depth: str,
    time_range: str | None,
    include_raw_content: bool,
) -> dict[str, object]:
    body: dict[str, object] = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_raw_content": include_raw_content,
    }
    if include_domains:
        body["include_domains"] = include_domains
    if exclude_domains:
        body["exclude_domains"] = exclude_domains
    if time_range:
        body["time_range"] = time_range

    request = urllib.request.Request(
        _DEFAULT_BASE_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.jarvis_tavily_api_key}",
        },
        method="POST",
    )
    ssl_context = _build_ssl_context()
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(settings.jarvis_tavily_timeout_ms, 1000) / 1000.0,
            context=ssl_context,
        ) as raw_response:
            payload = json.loads(raw_response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TavilySearchError(f"Tavily search failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise TavilySearchError(
                "Tavily search TLS verification failed. Install `certifi` in the backend environment, "
                "or configure JARVIS_TAVILY_CA_BUNDLE / SSL_CERT_FILE / OPENAI_CA_BUNDLE."
            ) from exc
        raise TavilySearchError(f"Tavily search failed: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise TavilySearchError("Tavily search returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise TavilySearchError("Tavily search returned an unexpected response payload.")
    return payload


def _parse_results(payload: dict[str, object], *, limit: int) -> list[TavilySearchItem]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []
    parsed: list[TavilySearchItem] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = _compact_text(item.get("title"), limit=_SNIPPET_LIMIT)
        url = _normalize_optional_string(item.get("url")) or ""
        if not title and not url:
            continue
        score_raw = item.get("score")
        score = float(score_raw) if isinstance(score_raw, (int, float)) else 0.0
        snippet = _compact_text(item.get("content"), limit=_SNIPPET_LIMIT)
        raw_excerpt = _compact_text(item.get("raw_content"), limit=_RAW_EXCERPT_LIMIT)
        parsed.append(
            TavilySearchItem(
                title=title or url,
                url=url,
                score=round(score, 4),
                snippet=snippet,
                raw_excerpt=raw_excerpt,
            )
        )
        if len(parsed) >= limit:
            break
    return parsed


def _grade_evidence(results: list[TavilySearchItem]) -> str:
    if not results:
        return "weak"
    top = results[0]
    second = results[1] if len(results) > 1 else None
    top_domain = _domain_from_url(top.url)
    if top.score >= 0.85 and top.snippet and top_domain.endswith("nba.com"):
        return "strong"
    if second and top.score >= 0.85 and second.score >= 0.7 and top.snippet and second.snippet:
        return "strong"
    if top.score >= 0.65 and (top.snippet or top.raw_excerpt):
        return "medium"
    return "weak"


def _suggested_answer(results: list[TavilySearchItem], evidence_quality: str) -> str | None:
    if not results:
        return None
    top = results[0]
    if top.snippet:
        prefix = {
            "strong": "Top evidence indicates:",
            "medium": "Available search results suggest:",
            "weak": "Limited search evidence suggests:",
        }.get(evidence_quality, "Search results suggest:")
        return f"{prefix} {top.snippet}"
    if top.title:
        return f"Most relevant result: {top.title}"
    return None


def _should_retry_without_domains(payload: dict[str, object]) -> bool:
    return not _parse_results(payload, limit=2)


def _build_ssl_context() -> ssl.SSLContext:
    cafile = (
        settings.jarvis_tavily_ca_bundle
        or os.getenv("SSL_CERT_FILE", "").strip()
        or os.getenv("OPENAI_CA_BUNDLE", "").strip()
    )
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = None
    return ssl.create_default_context(cafile=cafile)


def _normalize_domains(domains: list[str] | None) -> list[str]:
    if not isinstance(domains, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in domains:
        value = _normalize_optional_string(item)
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def _default_include_domains(query: str) -> list[str]:
    lowered = query.lower()
    if any(token in lowered for token in _NBA_HINT_PATTERNS):
        return ["nba.com"]
    return []


def _normalize_search_depth(value: str | None) -> str:
    normalized = _normalize_optional_string(value)
    if normalized in {"advanced", "basic"}:
        return normalized
    return "basic"


def _normalize_time_range(value: object) -> str | None:
    normalized = _normalize_optional_string(value)
    if not normalized:
        return None
    return _TIME_RANGE_ALIASES.get(normalized.lower())


def _normalize_optional_string(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _compact_text(value: object, *, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""
