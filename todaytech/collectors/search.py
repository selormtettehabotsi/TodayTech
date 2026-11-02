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

    async def _search(self, query: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        # Focus on top 12 targeted queries per search engine to prevent free quota burnout and lengthy scans
        selected = queries[:12] if len(queries) > 12 else queries
        for query in selected:
            try:
                items = await self._search(query)
                results.extend(items)
            except Exception as exc:
                log.warning("search.query_failed", collector=self.name, query=query, error=str(exc))
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
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
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

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
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

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
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
