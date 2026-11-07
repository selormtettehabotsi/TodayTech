"""Search API collectors — Tavily, Brave, Google, Serper, SerpAPI.

Uses ``site:`` queries to surface public posts from LinkedIn, Facebook, etc.
legitimately through search engines.  Rotates providers when quota is low.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from todaytech.collectors import Collector, register_collector

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Base search collector
# ---------------------------------------------------------------------------


class SearchCollector(Collector):
    """Base for search API collectors."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._exhausted: bool = False

    async def _search(self, query: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        # Select top 6 high-priority queries per provider for balanced coverage and fast scans
        selected = queries[:6] if len(queries) > 6 else queries
        for query in selected:
            if getattr(self, "_exhausted", False):
                break
            try:
                items = await self._search(query)
                results.extend(items)
            except Exception as exc:
                log.warning("search.query_failed", collector=self.name, query=query, error=str(exc))
                err_lower = str(exc).lower()
                if any(
                    w in err_lower for w in ("quota", "429", "401", "402", "exhausted", "credit")
                ):
                    log.info("search.provider_stopped_quota_limit", collector=self.name)
                    self._exhausted = True
                    break
        return results


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------


@register_collector("tavily")
class TavilyCollector(SearchCollector):
    """Tavily Search API collector."""

    @property
    def is_available(self) -> bool:
        return bool(self.settings.tavily_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _search(self, query: str) -> list[dict[str, Any]]:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": 10,
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (401, 402, 403, 429, 432):
                log.info("tavily.quota_exhausted_or_invalid", status=resp.status_code)
                self._exhausted = True
                return []
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "source_url": item.get("url", ""),
                    "raw_text": item.get("content", ""),
                    "source_name": "tavily",
                }
            )
        return results


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------


@register_collector("brave")
class BraveSearchCollector(SearchCollector):
    """Brave Search API collector."""

    @property
    def is_available(self) -> bool:
        return bool(self.settings.brave_search_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _search(self, query: str) -> list[dict[str, Any]]:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.settings.brave_search_api_key,
        }
        params: dict[str, str | int] = {"q": query, "count": 10}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "source_url": item.get("url", ""),
                    "raw_text": item.get("description", ""),
                    "source_name": "brave",
                }
            )
        return results


# ---------------------------------------------------------------------------
# Google Programmable Search
# ---------------------------------------------------------------------------


@register_collector("google")
class GoogleSearchCollector(SearchCollector):
    """Google Programmable Search API collector."""

    @property
    def is_available(self) -> bool:
        return bool(self.settings.google_search_api_key and self.settings.google_search_cx)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _search(self, query: str) -> list[dict[str, Any]]:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.settings.google_search_api_key,
            "cx": self.settings.google_search_cx,
            "q": query,
            "num": 10,
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 402, 403, 429, 432):
                log.info("google.quota_exhausted_or_invalid", status=resp.status_code)
                self._exhausted = True
                return []
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("items", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "source_url": item.get("link", ""),
                    "raw_text": item.get("snippet", ""),
                    "source_name": "google",
                }
            )
        return results


# ---------------------------------------------------------------------------
# Serper
# ---------------------------------------------------------------------------


@register_collector("serper")
class SerperCollector(SearchCollector):
    """Serper.dev search API collector."""

    @property
    def is_available(self) -> bool:
        return bool(self.settings.serper_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _search(self, query: str) -> list[dict[str, Any]]:
        url = "https://google.serper.dev/search"
        headers = {
            "X-API-KEY": self.settings.serper_api_key,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": 10}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (401, 402, 403, 429, 432):
                log.info("serper.quota_exhausted_or_invalid", status=resp.status_code)
                self._exhausted = True
                return []
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "source_url": item.get("link", ""),
                    "raw_text": item.get("snippet", ""),
                    "source_name": "serper",
                }
            )
        return results


# ---------------------------------------------------------------------------
# SerpAPI
# ---------------------------------------------------------------------------


@register_collector("serpapi")
class SerpAPICollector(SearchCollector):
    """SerpAPI collector."""

    @property
    def is_available(self) -> bool:
        return bool(self.settings.serpapi_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _search(self, query: str) -> list[dict[str, Any]]:
        url = "https://serpapi.com/search"
        params = {
            "api_key": self.settings.serpapi_api_key,
            "q": query,
            "engine": "google",
            "num": 10,
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 402, 403, 429, 432):
                log.info("google.quota_exhausted_or_invalid", status=resp.status_code)
                self._exhausted = True
                return []
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic_results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "source_url": item.get("link", ""),
                    "raw_text": item.get("snippet", ""),
                    "source_name": "serpapi",
                }
            )
        return results
