"""Server-side page verification and deadline validation against official pages."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

from todaytech.core.dedupe import _NON_OFFICIAL_DOMAINS, is_official_domain

log = structlog.get_logger()

# Disallowed aggregator, directory, blog, and social media domains
DISALLOWED_DOMAINS = _NON_OFFICIAL_DOMAINS

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_disallowed_domain(url: str, organizer: str = "") -> tuple[bool, str]:
    """Check if the URL belongs to an aggregator, blog or social media site."""
    if not is_official_domain(url, organizer):
        try:
            hostname = (urlparse(url).hostname or "").lower()
        except Exception:
            hostname = "invalid_url"
        return True, hostname or "non_official_domain"
    return False, ""


def looks_script_rendered(html: str, visible_text: str) -> bool:
    """Return True if the fetched page appears to be a client-side JavaScript rendered SPA."""
    lower_html = html.lower()

    if any(
        phrase in lower_html
        for phrase in (
            "enable javascript to run this app",
            "javascript is disabled",
            "please enable javascript",
            "requires javascript",
            "javascript must be enabled",
        )
    ):
        return True

    spa_containers = (
        'id="root"',
        'id="app"',
        'id="__next"',
        "<app-root>",
        "window.__next_data__",
        "window.__nuxt__",
    )
    has_spa_container = any(c in lower_html for c in spa_containers)
    if has_spa_container and len(visible_text.strip()) < 800:
        return True

    if len(visible_text.strip()) < 200 and "<script" in lower_html:
        return True

    return False


async def fetch_page_with_headless_browser(url: str, timeout: float = 15.0) -> str | None:
    """Task 6: Render client-side page with Playwright headless browser."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent=_BROWSER_HEADERS["User-Agent"])
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            content = await page.content()
            await browser.close()
            return content
    except Exception as exc:
        log.warning("verification.playwright_fetch_failed", url=url, error=str(exc))
        return None


def generate_date_patterns(d: date) -> list[re.Pattern]:
    """Generate regular expressions for common textual and numerical date representations."""
    day = d.day
    day_str_2 = f"{d.day:02d}"
    day_ord = f"{day}(?:st|nd|rd|th)"
    month_name = d.strftime("%B")  # e.g., April
    month_abbr = d.strftime("%b")  # e.g., Apr
    year = d.year

    month_pat = rf"(?:{month_name}|{month_abbr}\.?)"
    day_pat = rf"(?:{day_ord}|{day_str_2}|{day})"

    patterns = [
        # ISO / numerical formats: 2027-04-08, 2027/04/08, 04/08/2027, 4/8/2027, 08/04/2027
        re.compile(rf"\b{year}[-/]{d.month:02d}[-/]{day_str_2}\b", re.IGNORECASE),
        re.compile(
            rf"\b(?:0?{d.month}[-/]0?{day}[-/]{year}|0?{day}[-/]0?{d.month}[-/]{year})\b",
            re.IGNORECASE,
        ),
        # Month Day, Year: "April 8, 2027", "April 8th, 2027", "Apr 08, 2027"
        re.compile(
            rf"\b{month_pat}\s+{day_pat},?\s*(?:at\s+[\d:]+\s*(?:am|pm|utc|gmt)?,?\s*)?{year}\b",
            re.IGNORECASE,
        ),
        # Day Month Year: "8 April 2027", "8th April 2027", "08 April 2027"
        re.compile(
            rf"\b{day_pat}\s+(?:of\s+)?{month_pat},?\s*(?:at\s+[\d:]+\s*(?:am|pm|utc|gmt)?,?\s*)?{year}\b",
            re.IGNORECASE,
        ),
        # Year Month Day: "2027 April 8"
        re.compile(rf"\b{year}\s+{month_pat}\s+{day_pat}\b", re.IGNORECASE),
        # Proximity match within same clause/sentence (no periods, no line breaks, no other 4-digit year in between)
        re.compile(
            rf"\b{month_pat}\s+{day_pat}\b(?:(?!19\d\d|20\d\d)[^\.\n]){{0,35}}\b{year}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{day_pat}\s+{month_pat}\b(?:(?!19\d\d|20\d\d)[^\.\n]){{0,35}}\b{year}\b",
            re.IGNORECASE,
        ),
    ]
    return patterns


def extract_evidence_snippet(text: str, match_span: tuple[int, int], window: int = 120) -> str:
    """Extract a clean single-line snippet surrounding the matched text."""
    start, end = match_span
    snippet_start = max(0, start - window // 2)
    snippet_end = min(len(text), end + window // 2)
    raw = text[snippet_start:snippet_end].replace("\n", " ")
    return " ".join(raw.split())


async def verify_official_page_deadline(
    source_url: str,
    deadline: date,
    today_date: date,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, str, str | None, str]:
    """Fetch source_url and verify that stated deadline appears on the official page.

    Returns:
        (is_verified, resolved_status, evidence_snippet, explanation)
    """
    if not source_url:
        return False, "unknown", None, "Missing source_url"

    # 1. Domain verification: reject aggregators, blogs, social media
    disallowed, matched_domain = is_disallowed_domain(source_url)
    if disallowed:
        msg = f"Disallowed domain: '{matched_domain}' is an aggregator/blog/social site, not official organizer domain."
        log.warning("verification.disallowed_domain", url=source_url, domain=matched_domain)
        return False, "estimated", None, msg

    # 2. Fetch page in code
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=_BROWSER_HEADERS)
        close_client = True

    try:
        resp = await client.get(source_url)
        if resp.status_code != 200:
            msg = f"Page fetch returned HTTP {resp.status_code} on {today_date}."
            log.warning("verification.fetch_http_error", url=source_url, status=resp.status_code)
            return False, "estimated", None, msg

        html = resp.text
        # Strip script and style tags to examine visible content
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        visible_text = soup.get_text(separator=" ", strip=True)

        # 3. Check for deadline in visible text
        patterns = generate_date_patterns(deadline)
        for pat in patterns:
            match = pat.search(visible_text)
            if match:
                snippet = extract_evidence_snippet(visible_text, match.span())
                log.info(
                    "verification.confirmed_on_page",
                    url=source_url,
                    deadline=str(deadline),
                    snippet=snippet,
                )
                return True, "confirmed", snippet, f"Verified on official page: '{snippet}'"

        # Task 6: Script-rendered pages
        # If plain page fetch finds no dates and looks script-rendered, retry with Playwright headless browser before downgrading
        if looks_script_rendered(html, visible_text):
            log.info(
                "verification.script_rendered_detected",
                url=source_url,
                action="retry_with_headless_browser",
            )
            rendered_html = await fetch_page_with_headless_browser(source_url)
            if rendered_html:
                soup_rendered = BeautifulSoup(rendered_html, "html.parser")
                for tag in soup_rendered(["script", "style", "noscript"]):
                    tag.decompose()
                rendered_text = soup_rendered.get_text(separator=" ", strip=True)

                for pat in patterns:
                    match = pat.search(rendered_text)
                    if match:
                        snippet = extract_evidence_snippet(rendered_text, match.span())
                        log.info(
                            "verification.confirmed_via_headless_browser",
                            url=source_url,
                            deadline=str(deadline),
                            snippet=snippet,
                        )
                        return (
                            True,
                            "confirmed",
                            snippet,
                            f"Verified on script-rendered page via headless browser: '{snippet}'",
                        )

        # Deadline not found on official page
        msg = f"Fetched on {today_date}: official page did not contain stated deadline {deadline}."
        log.warning(
            "verification.deadline_not_found_on_page", url=source_url, deadline=str(deadline)
        )
        return False, "estimated", None, msg

    except Exception as exc:
        msg = f"Page fetch failed on {today_date}: {exc}"
        log.warning("verification.fetch_exception", url=source_url, error=str(exc))
        return False, "estimated", None, msg
    finally:
        if close_client:
            await client.aclose()
