"""RSS/Atom feed collector using feedparser with async HTTP client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import feedparser
import httpx
import structlog
import yaml

from todaytech.collectors import Collector, register_collector

log = structlog.get_logger()

# Default reliable RSS feeds for opportunity discovery
DEFAULT_FEEDS = [
    "https://www.opportunitydesk.org/feed/",
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@register_collector("rss")
class RSSCollector(Collector):
    """Collect opportunities from RSS/Atom feeds safely with timeouts."""

    @property
    def is_available(self) -> bool:
        return True

    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        # Gather feeds from defaults + sources.yaml + source packs
        feeds = list(DEFAULT_FEEDS)
        feeds.extend(self._load_user_feeds())

        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=_HEADERS) as client:
            for feed_url in feeds:
                try:
                    resp = await client.get(feed_url)
                    if resp.status_code == 200:
                        feed = feedparser.parse(resp.content)
                        for entry in feed.entries[:15]:
                            results.append(
                                {
                                    "title": entry.get("title", ""),
                                    "source_url": entry.get("link", ""),
                                    "raw_text": entry.get("summary", "")[:500],
                                    "source_name": f"rss:{feed.feed.get('title', feed_url)[:30]}",
                                }
                            )
                        log.debug("rss.parsed", url=feed_url, entries=len(feed.entries))
                except Exception as exc:
                    log.warning("rss.feed_failed", url=feed_url, error=str(exc))

        return results

    def _load_user_feeds(self) -> list[str]:
        """Load additional feed URLs from sources.yaml."""
        sources_path = Path("sources.yaml")
        if not sources_path.exists():
            return []
        try:
            with open(sources_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return [
                s["value"]
                for s in data.get("sources", [])
                if s.get("type") == "rss" and s.get("enabled", True)
            ]
        except Exception as exc:
            log.warning("rss.sources_load_failed", error=str(exc))
            return []
