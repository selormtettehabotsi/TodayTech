"""Server-side page verification and deadline validation against official pages."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

log = structlog.get_logger()

# Disallowed aggregator, directory, blog, and social media domains
DISALLOWED_DOMAINS = {
    # Aggregators and job/scholarship boards
    "devpost.com",
    "opportunitydesk.org",
    "scholarshipbob.com",
    "scholarships.jobzhiring.com",
    "youthop.com",
    "youthopportunitieshub.com",
    "advance-africa.com",
    "unstop.com",
    "afterschoolafrica.com",
    "scholarshipregion.com",
    "globalscholarships.com",
    "grabascholarship.com",
    "indiatoday.in",
    "hackerearth.com",
    "eventbrite.com",
    "meetup.com",
    "scholarshipstory.com",
    "opportunitiesforafricans.com",
    "scholarshiptab.com",
    "wemakescholars.com",
    "scholars4dev.com",
    "heysuccess.com",
    "armacad.info",
    "fundsforngos.org",
    "opportunitiescircle.com",
    "opportunitiescorner.com",
    "fullyfundedaffairs.com",
    # Social media & user-generated forums
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "t.me",
    "telegram.me",
    "reddit.com",
    "medium.com",
    "substack.com",
    "youtube.com",
    "quora.com",
    "tiktok.com",
    # Generic blogging / site builders
    "wordpress.com",
    "blogspot.com",
    "tumblr.com",
    "wixsite.com",
    "weebly.com",
    "notion.site",
    "canva.site",
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_disallowed_domain(url: str) -> tuple[bool, str]:
    """Check if the URL belongs to an aggregator, blog or social media site."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return True, "missing_hostname"
        for disallowed in DISALLOWED_DOMAINS:
            if hostname == disallowed or hostname.endswith("." + disallowed):
                return True, disallowed
        return False, ""
    except Exception:
        return True, "invalid_url"


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
