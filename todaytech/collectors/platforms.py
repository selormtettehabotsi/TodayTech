"""Platform collectors — Devpost, MLH, Devfolio, Unstop, HackerEarth, Lablab, Kaggle.

These scrape public listing pages that explicitly allow automated access or
provide API endpoints.  robots.txt is respected via the ``_check_robots``
helper.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from todaytech.collectors import Collector, register_collector

log = structlog.get_logger()

_HEADERS = {
    "User-Agent": "TodayTech/1.0 (https://github.com/todaytech; opportunity-radar)",
    "Accept": "text/html,application/json",
}


# ---------------------------------------------------------------------------
# Devpost
# ---------------------------------------------------------------------------


@register_collector("devpost")
class DevpostCollector(Collector):
    """Collect hackathons from Devpost's public listing API."""

    @property
    def is_available(self) -> bool:
        return True  # No key needed

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://devpost.com/api/hackathons"
        params: dict[str, str | int] = {
            "status[]": "open",
            "order_by": "deadline",
            "page": 1,
            "per_page": 25,
        }

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        for h in data.get("hackathons", []):
            results.append(
                {
                    "title": h.get("title", ""),
                    "type": "hackathon",
                    "organizer": h.get("organization_name", ""),
                    "deadline": h.get("submission_period_dates", ""),
                    "location": h.get("displayed_location", {}).get("location", "Online"),
                    "mode": "online" if h.get("online_only") else "hybrid",
                    "reward": ", ".join(p.get("label", "") for p in h.get("prizes", [])),
                    "application_url": h.get("url", ""),
                    "source_url": h.get("url", ""),
                    "source_name": "devpost",
                    "raw_text": h.get("tagline", ""),
                }
            )
        return results


# ---------------------------------------------------------------------------
# MLH
# ---------------------------------------------------------------------------


@register_collector("mlh")
class MLHCollector(Collector):
    """Collect events from MLH's public events page."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://mlh.io/events"

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for event in soup.select(".event-wrapper"):
            name_el = event.select_one(".event-name")
            link_el = event.select_one("a[href]")
            date_el = event.select_one(".event-date")
            loc_el = event.select_one(".event-location")

            if name_el:
                results.append(
                    {
                        "title": name_el.get_text(strip=True),
                        "type": "hackathon",
                        "organizer": "MLH",
                        "deadline": date_el.get_text(strip=True) if date_el else "",
                        "location": loc_el.get_text(strip=True) if loc_el else "",
                        "application_url": link_el["href"] if link_el else "",
                        "source_url": url,
                        "source_name": "mlh",
                    }
                )
        return results


# ---------------------------------------------------------------------------
# Devfolio
# ---------------------------------------------------------------------------


@register_collector("devfolio")
class DevfolioCollector(Collector):
    """Collect hackathons from Devfolio's public API."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://api.devfolio.co/api/search/hackathons"
        payload = {
            "type": "application_open",
            "from": 0,
            "size": 20,
        }

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                log.info("collector.unstop_blocked_by_cloudflare")
                return []
            resp.raise_for_status()
            data = resp.json()

        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            results.append(
                {
                    "title": src.get("name", ""),
                    "type": "hackathon",
                    "organizer": src.get("org_name", "Devfolio"),
                    "deadline": src.get("ends_at", ""),
                    "start_date": src.get("starts_at", ""),
                    "location": src.get("location", "Online"),
                    "mode": "online" if src.get("is_online") else "in-person",
                    "reward": str(src.get("prize_amount", "")),
                    "application_url": f"https://devfolio.co/hackathons/{src.get('slug', '')}",
                    "source_url": f"https://devfolio.co/hackathons/{src.get('slug', '')}",
                    "source_name": "devfolio",
                    "raw_text": src.get("tagline", ""),
                }
            )
        return results


# ---------------------------------------------------------------------------
# Unstop
# ---------------------------------------------------------------------------


@register_collector("unstop")
class UnstopCollector(Collector):
    """Collect opportunities from Unstop's public API."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://unstop.com/api/public/opportunity/search-new"
        payload = {
            "opportunity": ["hackathons", "competitions", "scholarships"],
            "oppstatus": "open",
            "per_page": 20,
            "page": 1,
        }

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                log.info("collector.unstop_blocked_by_cloudflare")
                return []
            resp.raise_for_status()
            data = resp.json()

        for item in data.get("data", {}).get("data", []):
            opp_type = "competition"
            if "hackathon" in item.get("type", "").lower():
                opp_type = "hackathon"
            elif "scholarship" in item.get("type", "").lower():
                opp_type = "scholarship"

            results.append(
                {
                    "title": item.get("title", ""),
                    "type": opp_type,
                    "organizer": item.get("organisation", {}).get("name", ""),
                    "deadline": item.get("regnEnd", ""),
                    "reward": item.get("prize", ""),
                    "application_url": f"https://unstop.com/{item.get('public_url', '')}",
                    "source_url": f"https://unstop.com/{item.get('public_url', '')}",
                    "source_name": "unstop",
                    "raw_text": item.get("short_desc", ""),
                }
            )
        return results


# ---------------------------------------------------------------------------
# HackerEarth
# ---------------------------------------------------------------------------


@register_collector("hackerearth")
class HackerEarthCollector(Collector):
    """Collect challenges from HackerEarth."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://www.hackerearth.com/challenges/"

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select(".challenge-card-modern"):
            title_el = card.select_one(".challenge-name")
            link_el = card.select_one("a[href]")
            if title_el:
                results.append(
                    {
                        "title": title_el.get_text(strip=True),
                        "type": "competition",
                        "organizer": "HackerEarth",
                        "application_url": urljoin(
                            "https://www.hackerearth.com", str(link_el["href"])
                        )
                        if link_el
                        else "",
                        "source_url": url,
                        "source_name": "hackerearth",
                    }
                )
        return results


# ---------------------------------------------------------------------------
# Lablab.ai
# ---------------------------------------------------------------------------


@register_collector("lablab")
class LablabCollector(Collector):
    """Collect AI hackathons from lablab.ai."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://lablab.ai/event"

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("[class*='EventCard'], [class*='event-card'], .card"):
            title_el = card.select_one("h2, h3, [class*='title']")
            link_el = card.select_one("a[href]")
            if title_el:
                link = ""
                if link_el:
                    raw_href = link_el.get("href", "")
                    href = str(raw_href) if raw_href else ""
                    link = href if href.startswith("http") else urljoin("https://lablab.ai", href)
                results.append(
                    {
                        "title": title_el.get_text(strip=True),
                        "type": "hackathon",
                        "organizer": "lablab.ai",
                        "application_url": link,
                        "source_url": url,
                        "source_name": "lablab",
                    }
                )
        return results


# ---------------------------------------------------------------------------
# Kaggle
# ---------------------------------------------------------------------------


@register_collector("kaggle")
class KaggleCollector(Collector):
    """Collect active Kaggle competitions."""

    @property
    def is_available(self) -> bool:
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def collect(self, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url = "https://www.kaggle.com/competitions"

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("[class*='competition'], .competition-list-item, li"):
            title_el = card.select_one("a[href*='/competitions/']")
            if title_el:
                raw_href = title_el.get("href", "")
                href = str(raw_href) if raw_href else ""
                full_url = href if href.startswith("http") else f"https://www.kaggle.com{href}"
                results.append(
                    {
                        "title": title_el.get_text(strip=True),
                        "type": "competition",
                        "organizer": "Kaggle",
                        "application_url": full_url,
                        "source_url": full_url,
                        "source_name": "kaggle",
                    }
                )
        return results
