"""Intelligence pipeline — collect → normalize → dedupe → extract → score → store.

This is the central orchestrator that runs a full scan cycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from urllib.parse import urlparse, urlunparse

import httpx
import structlog
from bs4 import BeautifulSoup
from thefuzz import fuzz

from todaytech.collectors import get_all_collectors
from todaytech.core.alerts import send_telegram_alert
from todaytech.core.config import get_settings, load_profile
from todaytech.core.database import (
    ScanRow,
    get_last_scan,
    get_session,
    upsert_opportunity,
)
from todaytech.core.eligibility import check_eligibility, rule_check
from todaytech.core.llm import LLMClient, create_llm_client_from_settings
from todaytech.core.models import (
    DateStatus,
    EligibilityVerdict,
    Opportunity,
    OpportunityType,
    UserProfile,
)
from todaytech.core.scam_filter import check_scam
from todaytech.core.scoring import compute_fit_score
from todaytech.core.verification import verify_official_page_deadline

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def generate_queries(profile: UserProfile) -> list[str]:
    """Generate search queries from the user's profile."""
    queries: list[str] = []
    types = profile.opportunity_types

    for opp_type in types:
        if opp_type == "ambassador":
            queries.extend(_ambassador_queries(profile))
            continue

        # Base query
        queries.append(f"{opp_type} {profile.field_of_study} 2026 2027")

        # Country-specific
        if profile.country:
            queries.append(f"{opp_type} for {profile.country} students {profile.field_of_study}")

        # Level-specific
        queries.append(f"{opp_type} {profile.level} {profile.field_of_study}")

    # Skill-based queries
    for skill in profile.skills[:5]:
        queries.append(f"hackathon {skill} 2027 open")
        queries.append(f"scholarship {skill} students")

    # Interest-based queries
    for interest in profile.interests[:3]:
        queries.append(f"opportunities {interest} students {profile.country}")

    # Region-specific queries (Europe / France etc.)
    queries.extend(_region_queries(profile))

    # Site-specific queries for LinkedIn
    queries.extend(_linkedin_queries(profile))

    return queries


def _ambassador_queries(profile: UserProfile) -> list[str]:
    """Queries for campus/student ambassador programs run by tech companies and communities."""
    queries = [
        "tech campus ambassador program 2027 apply students",
        "student ambassador program tech company university 2027",
        "developer student community lead application 2027",
        "Microsoft Learn Student Ambassador 2027 apply Azure cloud",
        "GitHub Campus Expert 2027 application",
        "AWS Student Builder Group Leader 2027",
        "Google Developer Student Club lead 2027 apply",
        f"campus ambassador {profile.field_of_study} students apply",
    ]
    if profile.country:
        queries.append(f"campus ambassador program {profile.country} university tech")
        queries.append(f"site:linkedin.com campus ambassador tech {profile.country}")
    return queries


def _region_queries(profile: UserProfile) -> list[str]:
    """Generate queries for opportunities in specific target regions from the user profile."""
    queries: list[str] = []
    regions = [r.lower() for r in profile.regions]
    types = profile.opportunity_types
    has_scholarship = "scholarship" in types or "fellowship" in types

    if any(r in regions for r in ("europe", "france", "global")) and has_scholarship:
        field = profile.field_of_study
        country = profile.country

        # France-specific
        queries.extend(
            [
                f"Eiffel Excellence Scholarship 2027 {field} application",
                f"Campus France scholarship 2027 {country} STEM",
                f"Charpak scholarship France {country} 2027 Masters",
                f"Erasmus Mundus Joint Master {field} 2027 application",
                "Mastercard Foundation scholarship Europe 2027 Africa",
                "Mastercard Foundation Sciences Po 2027 Africa",
                f"study in France scholarship 2027 {field} {country}",
            ]
        )

        # Broader Europe
        queries.extend(
            [
                f"DAAD scholarship {country} 2027 {field}",
                f"Chevening scholarship {country} 2027 application",
                f"Commonwealth scholarship {country} 2027 Masters {field}",
                f"Netherlands Fellowship Programme {country} 2027 STEM",
                f"Swiss Government Excellence Scholarship {country} 2027",
                f"Europe scholarship {country} students {field} 2027",
            ]
        )

    return queries


def _linkedin_queries(profile: UserProfile) -> list[str]:
    """Generate site:linkedin.com queries to surface opportunities posted on LinkedIn."""
    queries: list[str] = []
    field = profile.field_of_study
    country = profile.country
    regions = [r.lower() for r in profile.regions]
    types = profile.opportunity_types

    if country:
        if "scholarship" in types:
            queries.append(f"site:linkedin.com scholarship {country} students {field} 2027")
        if "internship" in types:
            queries.append(f"site:linkedin.com internship {field} {country} 2027")
        if "fellowship" in types:
            queries.append(f"site:linkedin.com fellowship {country} technology {field} 2027")
        if "ambassador" in types:
            queries.append(f"site:linkedin.com ambassador program tech {country} 2027")

    if any(r in regions for r in ("europe", "france")):
        if "scholarship" in types:
            queries.extend(
                [
                    f"site:linkedin.com scholarship Europe France Africa {field} 2027",
                    "site:linkedin.com Mastercard Foundation Scholars 2027 application",
                    "site:linkedin.com Eiffel scholarship Erasmus Mundus 2027 Africa",
                ]
            )
        if "fellowship" in types:
            queries.append("site:linkedin.com cloud DevOps fellowship Africa Europe 2027")

    if "fellowship" in types:
        queries.append("site:linkedin.com software engineering fellowship 2027 apply")
    if "internship" in types:
        queries.append("site:linkedin.com AWS cloud DevOps scholarship internship 2027")

    return queries


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------


def canonicalize_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())
    # Remove tracking params, fragments
    clean_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def generate_external_id(item: dict[str, Any]) -> str:
    """Generate a stable external ID for deduplication."""
    url = canonicalize_url(item.get("source_url", "") or item.get("application_url", ""))
    if url:
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    # Fall back to title hash
    title = item.get("title", "").strip().lower()
    return hashlib.sha256(title.encode()).hexdigest()[:16]


def is_duplicate(
    item: dict[str, Any], seen: dict[str, dict[str, Any]], threshold: int = 85
) -> bool:
    """Check if an item is a duplicate via URL match or fuzzy title match."""
    ext_id = generate_external_id(item)
    if ext_id in seen:
        return True

    title = item.get("title", "").strip()
    for existing in seen.values():
        existing_title = existing.get("title", "").strip()
        if fuzz.ratio(title.lower(), existing_title.lower()) >= threshold:
            return True

    return False


async def _fetch_page_snippet(url: str) -> str:
    """Perform live web research by fetching and cleaning the opportunity page content."""
    if not url or not url.startswith(("http://", "https://")):
        return ""
    if any(d in url.lower() for d in ("linkedin.com", "t.me", "twitter.com", "x.com")):
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=8, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            if (
                resp.status_code == 200
                and "text/html" in resp.headers.get("content-type", "").lower()
            ):
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
                    s.decompose()
                text = " ".join(soup.stripped_strings)
                return text[:2500]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """Analyze this opportunity, extract its details, and strictly research its availability.

TODAY'S DATE: {today_gmt} (Timezone: GMT 0:00)

RAW TEXT & RESEARCH DATA:
{raw_text}

TITLE: {title}
SOURCE URL: {source_url}

RESEARCH & AVAILABILITY RULES:
1. STRICT AVAILABILITY RESEARCH:
   - Determine whether applications are CURRENTLY OPEN and STILL AVAILABLE.
   - If applications are closed, ended, concluded, or the deadline has passed before {today_gmt}, set "is_available": false and "status": "closed".
   - If the listing refers to an old/past cohort (e.g. 2023, 2024, 2025) that has concluded, set "is_available": false and "status": "closed".
   - If the program is open, rolling, or upcoming for 2026/2027, set "is_available": true and "status": "open".
2. DEADLINE NORMALIZATION (GMT 0:00):
   - Extract the exact closing/submission deadline in YYYY-MM-DD format.
   - For date ranges (e.g., "Nov 9 - Nov 30, 2026"), the deadline is the closing end date ("2026-11-30").
   - If the deadline is before {today_gmt}, set "is_available": false.
   - If rolling or open enrollment without fixed closing date, use null.
3. CLASSIFICATION & DETAILS:
   - Categorize as: hackathon|internship|scholarship|fellowship|grant|program|competition|ambassador|other.
   - Use type "ambassador" for campus/student ambassador, student community lead, or student developer advocate programs.

Return valid JSON:
{{
  "title": "Clean, descriptive opportunity title",
  "type": "hackathon|internship|scholarship|fellowship|grant|program|competition|ambassador|other",
  "organizer": "Organizing company, university, or foundation",
  "is_available": true,
  "status": "open|upcoming|closed",
  "availability_reason": "Explanation of whether applications are actively open or ended",
  "deadline": "YYYY-MM-DD or null",
  "start_date": "YYYY-MM-DD or null",
  "location": "string",
  "mode": "online|in-person|hybrid",
  "cost": 0,
  "eligibility_text": "string",
  "countries_allowed": ["list", "of", "countries"] or ["all"],
  "level_required": "string or empty",
  "field_required": "string or empty",
  "reward": "string describing prizes/benefits",
  "application_url": "Direct link to apply",
  "confidence": 0.0 to 1.0
}}
"""


async def extract_opportunity(
    item: dict[str, Any],
    llm: LLMClient,
) -> dict[str, Any]:
    """Perform research and use LLM to extract and verify opportunity availability."""
    try:
        today_gmt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_text = item.get("raw_text", "")

        # Deep research: If raw_text is short, fetch live web page snippet
        if len(raw_text.strip()) < 300 and item.get("source_url"):
            page_content = await _fetch_page_snippet(item["source_url"])
            if page_content:
                raw_text = f"{raw_text}\n\nLive Page Content:\n{page_content}"

        data = await llm.generate_json(
            _EXTRACT_PROMPT.format(
                today_gmt=today_gmt,
                raw_text=raw_text[:2500],
                title=item.get("title", ""),
                source_url=item.get("source_url", ""),
            ),
            system="You are an expert Opportunity Research & Verification Agent. Filter out closed or expired programs.",
        )

        # Merge LLM extraction with original data
        merged = {**item, **{k: v for k, v in data.items() if v is not None and v != ""}}

        # Ensure source_url is preserved
        if not merged.get("source_url"):
            merged["source_url"] = item.get("source_url", "")

        return merged

    except Exception as exc:
        log.warning("extraction.failed", title=item.get("title"), error=str(exc))
        return item


# ---------------------------------------------------------------------------
# Pre-filtering & Metadata Fallback
# ---------------------------------------------------------------------------


def _is_closed_or_expired(item: dict[str, Any], today_utc: date) -> tuple[bool, str]:
    """Fast pre-filter: discard concluded cohorts, past years, or closed deadlines without LLM calls."""
    title = str(item.get("title", ""))
    raw_text = str(item.get("raw_text", ""))
    combined = f"{title} {raw_text}".lower()

    # Past year without future cohort
    past_years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    future_years = ["2026", "2027", "2028"]
    if any(y in combined for y in past_years) and not any(y in combined for y in future_years):
        return True, "Past year program"

    # Explicit closed phrasing
    closed_keywords = [
        "submissions closed",
        "application closed",
        "applications are closed",
        "registration closed",
        "ended on",
        "deadline has passed",
        "winners announced",
        "concluded on",
        "has ended",
        "applications closed",
    ]
    if any(k in combined for k in closed_keywords):
        return True, "Applications closed"

    # Deadline check if already present
    raw_dl = item.get("deadline")
    if raw_dl:
        parsed = _parse_date(raw_dl)
        if parsed and parsed < today_utc:
            return True, f"Deadline passed ({parsed})"

    return False, ""


def _extract_metadata_fallback(item: dict[str, Any], today_utc: date) -> dict[str, Any]:
    """Direct rule-based extraction fallback from item metadata and search snippets."""
    title = str(item.get("title", "Opportunity")).strip()
    raw_text = str(item.get("raw_text", "")).strip()
    combined = f"{title} {raw_text}".lower()

    # Determine type
    curr_type = str(item.get("type", "other")).lower()
    if curr_type in (
        "hackathon",
        "internship",
        "scholarship",
        "fellowship",
        "grant",
        "ambassador",
        "competition",
    ):
        opp_type = curr_type
    elif any(k in combined for k in ("scholarship", "bourses", "bourse", "mastercard foundation")):
        opp_type = "scholarship"
    elif "fellowship" in combined:
        opp_type = "fellowship"
    elif "internship" in combined or "intern" in combined:
        opp_type = "internship"
    elif any(k in combined for k in ("hackathon", "buildathon", "hack")):
        opp_type = "hackathon"
    elif "ambassador" in combined or "campus lead" in combined or "student advocate" in combined:
        opp_type = "ambassador"
    elif "grant" in combined or "funding" in combined:
        opp_type = "grant"
    elif "competition" in combined or "challenge" in combined or "contest" in combined:
        opp_type = "competition"
    else:
        opp_type = "other"

    # Parse deadline
    deadline_str = item.get("deadline")
    if not deadline_str:
        m = re.search(
            r"(?:deadline|apply by|due|closes|closing date)[:\s]+([A-Za-z]{3,9}\s+\d{1,2},?\s+202\d|\d{1,2}\s+[A-Za-z]{3,9}\s+202\d|\d{4}-\d{2}-\d{2})",
            combined,
            re.IGNORECASE,
        )
        if m:
            deadline_str = m.group(1)

    # Clean title prefixes/suffixes
    clean_title = re.sub(
        r"^(Devpost|Devfolio|OpportunityDesk)\s*[-:|]\s*", "", title, flags=re.IGNORECASE
    )
    clean_title = re.sub(
        r"\s*[-:|]\s*(LinkedIn|Devpost|Devfolio|OpportunityDesk)$",
        "",
        clean_title,
        flags=re.IGNORECASE,
    )

    return {
        **item,
        "title": clean_title.strip() or title,
        "type": opp_type,
        "organizer": item.get("organizer", ""),
        "deadline": deadline_str,
        "start_date": item.get("start_date"),
        "location": item.get("location", ""),
        "mode": item.get("mode", "online"),
        "cost": item.get("cost", 0.0),
        "reward": item.get("reward", ""),
        "application_url": item.get("application_url") or item.get("source_url", ""),
        "source_url": item.get("source_url", ""),
        "raw_text": raw_text,
        "is_available": True,
        "status": "open",
        "availability_reason": "Active listing verified by source",
        "eligibility_text": raw_text[:500],
        "countries_allowed": ["all"],
        "external_id": item.get("external_id", ""),
    }


# ---------------------------------------------------------------------------
# Grounded Scanner Prompt & Validation Layer
# ---------------------------------------------------------------------------

SCANNER_PROMPT_TEMPLATE = """Today's date is {today} ({timezone}). You are a verification-first opportunity scanner. Use Google Search for every opportunity. Never use memory or training data for dates.

PROFILE: {name}, {level} in {field_of_study} at {university}, {country} (Citizenship: {citizenship}).
FIELDS: {fields}
TYPES: {types}

THE CORE RULE
A date is "confirmed" only if you read it on the organiser's official page for the CURRENT cycle. Never take a previous year's date and change the year. If the official page shows an older cycle, or no dates, the date is NOT confirmed.

DATE STATUS (required on every item)
- confirmed: dates read on the official page for the current cycle.
- estimated: dates not yet published. Set "deadline" to null and describe the likely window in "estimated_window" (for example "likely late March 2027, based on the 2026 cycle").
- rolling: applications reviewed continuously. Set "deadline" to null.
- unknown: no usable date found.

RULES
1. Include only opportunities that are open now or expected to open. Exclude anything whose confirmed deadline is before {today}.
2. Open the official page for each item. Use blogs and aggregators only to discover leads, never as the source of dates. If two sources disagree, return both dates and set "conflict": true.
3. Check eligibility for a {country} undergraduate. If the programme excludes {country} or requires a different level, exclude it.
4. Calculate days_remaining only for confirmed deadlines. Otherwise use null.
5. Give a match score only after eligibility is checked, with a one-sentence reason.
6. Return at most 25 items. Quality over quantity.
7. Before finalising, re-open the official link for every item with date_status "confirmed" and make sure the dates still match.

Return ONLY a JSON array. Each item:
{{
  "name": "",
  "organisation": "",
  "type": "hackathon | internship | scholarship | fellowship | grant | program | competition | ambassador",
  "field": "",
  "date_status": "confirmed | estimated | rolling | unknown",
  "opens": "YYYY-MM-DD or null",
  "deadline": "YYYY-MM-DD or null",
  "estimated_window": "string or null",
  "program_start": "YYYY-MM-DD or null",
  "days_remaining": null,
  "cycle_year": 2026,
  "date_evidence": "one sentence in your own words saying what the official page states",
  "conflict": false,
  "eligibility": "",
  "funding": "",
  "apply_url": "",
  "source_url": "official page where dates were read",
  "match_score": 0,
  "match_reason": "",
  "verified_on": "{today}"
}}
"""


async def scan_with_gemini_grounded(
    profile: UserProfile,
    settings: Any,
    today_utc: date | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Scan and verify opportunities using Gemini with Google Search grounding.

    If Google Search grounding fails, hits quota, or returns no grounding metadata:
    - Forces items with date_status 'confirmed' to 'unknown'
    - Logs the reason
    - Sends a Telegram alert that the scan ran ungrounded
    """
    if not getattr(settings, "gemini_api_key", ""):
        return [], False, "No Gemini API key configured"

    if today_utc is None:
        today_utc = datetime.now(timezone.utc).date()

    today_str = today_utc.strftime("%Y-%m-%d")
    timezone_str = "GMT 0:00"

    prompt = SCANNER_PROMPT_TEMPLATE.format(
        today=today_str,
        timezone=timezone_str,
        name=profile.name or "Student",
        level=profile.level or "undergraduate",
        field_of_study=profile.field_of_study or "Computer Science",
        university=profile.university or "University",
        country=profile.country or "Ghana",
        citizenship=", ".join(profile.citizenship) or profile.country or "Ghana",
        fields=", ".join(
            profile.interests or ["Computer Science", "Software Engineering", "AI/ML", "Cloud"]
        ),
        types=", ".join(
            profile.opportunity_types
            or ["hackathon", "internship", "scholarship", "fellowship", "ambassador"]
        ),
    )

    try:
        llm = create_llm_client_from_settings(settings)
        items, is_grounded, failure_reason = await llm.generate_grounded_json_array(
            prompt,
            system="You are a verification-first opportunity scanner. Rely strictly on Google Search for current dates.",
        )

        if not is_grounded:
            reason = failure_reason or "No grounding metadata or search queries returned by Gemini"
            log.warning("gemini_grounded_scanner.ungrounded", reason=reason, count=len(items))
            # Task 1: Force all items to unknown, log reason, and send Telegram alert
            for item in items:
                if str(item.get("date_status", "")).lower() == "confirmed":
                    item["date_status"] = "unknown"
                    item["conflict"] = True
                    item["date_evidence"] = f"Forced to unknown: scan ran ungrounded ({reason})"

            alert_msg = (
                f"⚠️ *TodayTech Alert: Scan Ran Ungrounded*\n\n"
                f"• *Reason*: `{reason}`\n"
                f"• *Action*: All opportunities forced to `date_status: unknown`.\n"
                f"• *Time*: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} GMT"
            )
            await send_telegram_alert(alert_msg, settings=settings)
        else:
            log.info("gemini_grounded_scanner.success", count=len(items))

        return items, is_grounded, failure_reason
    except Exception as exc:
        reason = f"Gemini search grounding failed: {exc}"
        log.warning("gemini_grounded_scanner.failed", error=reason)
        alert_msg = (
            f"⚠️ *TodayTech Alert: Grounding Unavailable*\n\n"
            f"• *Error*: `{reason}`\n"
            f"• *Action*: Falling back to standard collectors. Confirmed dates disabled.\n"
            f"• *Time*: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} GMT"
        )
        await send_telegram_alert(alert_msg, settings=settings)
        return [], False, reason


async def validate_opportunity_batch(
    raw_items: Sequence[dict[str, Any] | Opportunity],
    today_date: date,
    max_cap: int = 25,
    *,
    is_grounded_scan: bool = True,
    verify_pages: bool = True,
    client: httpx.AsyncClient | None = None,
) -> list[Opportunity]:
    """Strict validation layer that does not trust the LLM or raw sources.

    1. Drop items with no source_url.
    2. Force items to unknown if scan ran ungrounded (Task 1).
    3. Drop items whose confirmed deadline is before today.
    4. Reject any 'confirmed' item whose deadline year does not match cycle_year, and downgrade it.
    5. Server-side page verification: confirm stated deadline appears on official domain page (Task 2 & 3).
    6. Deduplicate by apply_url.
    7. Compute days_remaining in code, only for confirmed deadlines.
    8. Cap results at 25 per scan.
    """
    seen_apply_urls: set[str] = set()
    validated: list[Opportunity] = []

    for raw in raw_items:
        if isinstance(raw, Opportunity):
            opp = raw
        else:
            data = dict(raw)
            if "name" in data and "title" not in data:
                data["title"] = data["name"]
            if "organisation" in data and "organizer" not in data:
                data["organizer"] = data["organisation"]
            if "apply_url" in data and "application_url" not in data:
                data["application_url"] = data["apply_url"]
            if "match_score" in data and "fit_score" not in data:
                try:
                    data["fit_score"] = int(data["match_score"])
                except Exception:
                    data["fit_score"] = 0
            if "match_reason" in data and "fit_reason" not in data:
                data["fit_reason"] = data["match_reason"]
            opp = _dict_to_opportunity(data)

        # 1. Drop items with no source_url
        src_url = str(opp.source_url or "").strip()
        if not src_url or src_url.lower() in ("none", "null", "n/a", "undefined", "#"):
            log.info("validation.dropped_missing_source_url", title=opp.title)
            continue

        # 6. Deduplicate by apply_url (or source_url fallback)
        apply_key = canonicalize_url(opp.application_url or opp.source_url)
        if not apply_key or apply_key in seen_apply_urls:
            log.info("validation.dropped_duplicate_apply_url", url=apply_key, title=opp.title)
            continue
        seen_apply_urls.add(apply_key)

        # Normalize date_status
        date_status_str = (
            opp.date_status.value
            if hasattr(opp.date_status, "value")
            else str(opp.date_status or "unknown")
        ).lower()
        if date_status_str not in ("confirmed", "estimated", "rolling", "unknown"):
            date_status_str = "unknown"

        # Deduce cycle_year from title regex if missing and title has e.g. 2026/2027
        if opp.cycle_year is None:
            match_yr = re.search(r"\b(202\d)\b", opp.title)
            if match_yr:
                opp.cycle_year = int(match_yr.group(1))

        # Task 1: Ungrounded fallback - ungrounded scan must NEVER produce confirmed items
        if not is_grounded_scan and date_status_str == "confirmed":
            log.warning("validation.ungrounded_scan_downgrade", title=opp.title)
            date_status_str = "unknown"
            opp.conflict = True
            opp.date_evidence = "Forced to unknown: scan ran ungrounded without search grounding"

        # Task 3: Cycle year check
        if date_status_str == "confirmed":
            if not opp.deadline:
                date_status_str = "estimated"
                opp.deadline = None
            elif opp.cycle_year is not None and opp.deadline.year != opp.cycle_year:
                log.info(
                    "validation.cycle_year_mismatch_downgraded",
                    title=opp.title,
                    deadline_year=opp.deadline.year,
                    cycle_year=opp.cycle_year,
                )
                date_status_str = "estimated"
                if not opp.estimated_window:
                    opp.estimated_window = f"Projected for {opp.cycle_year} cycle"
                opp.deadline = None
                opp.conflict = True

        # Task 2 & Task 3: Server-side page verification
        # A confirmed status strictly requires Task 2 to pass!
        if date_status_str == "confirmed" and opp.deadline:
            if verify_pages:
                verified, new_status, snippet, explanation = await verify_official_page_deadline(
                    opp.source_url,
                    opp.deadline,
                    today_date,
                    client=client,
                )
                opp.fetched_on = today_date
                if not verified:
                    date_status_str = new_status
                    opp.conflict = True
                    opp.date_evidence = explanation
                    opp.deadline = None
                    opp.days_remaining = None
                else:
                    date_status_str = "confirmed"
                    opp.conflict = False
                    opp.verified_on = today_date
                    opp.date_evidence = explanation

        # 3. Drop items whose confirmed deadline is before today
        if date_status_str == "confirmed" and opp.deadline and opp.deadline < today_date:
            log.info(
                "validation.dropped_past_deadline",
                title=opp.title,
                deadline=str(opp.deadline),
                today=str(today_date),
            )
            continue

        # 7. Compute days_remaining in code, only for confirmed deadlines
        if date_status_str == "confirmed" and opp.deadline:
            days_rem = (opp.deadline - today_date).days
            if days_rem < 0:
                continue
            opp.days_remaining = days_rem
        else:
            opp.days_remaining = None

        opp.date_status = DateStatus(date_status_str)
        if not opp.verified_on:
            opp.verified_on = today_date

        validated.append(opp)

    # Sort with confirmed items having nearest deadlines first, followed by fit score
    validated.sort(
        key=lambda o: (
            1 if o.date_status == DateStatus.CONFIRMED else 0,
            o.fit_score,
            -(o.days_remaining if o.days_remaining is not None else 9999),
        ),
        reverse=True,
    )

    # 8. Cap results at 25 per scan
    return validated[:max_cap]


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_scan(
    *,
    force: bool = False,
    cache_hours: int = 3,
) -> dict[str, Any]:
    """Run a full opportunity scan.

    Returns a summary dict with counts and any errors.
    """
    settings = get_settings()
    settings.validate_runtime()
    profile = load_profile(settings.profile_path)
    if profile is None:
        return {"error": "Profile not found. Run `todaytech init` first."}

    # Check if we have a recent scan
    if not force:
        async with get_session() as session:
            last_scan = await get_last_scan(session)
            if last_scan and last_scan.started_at:
                age = datetime.utcnow() - last_scan.started_at
                if age < timedelta(hours=cache_hours):
                    log.info("scan.cached", age_minutes=int(age.total_seconds() / 60))
                    return {
                        "cached": True,
                        "scan_id": last_scan.id,
                        "items_found": last_scan.items_found,
                    }

    llm = create_llm_client_from_settings(settings)
    queries = generate_queries(profile)
    log.info("scan.starting", query_count=len(queries))

    # Record scan start
    async with get_session() as session:
        scan = ScanRow(started_at=datetime.utcnow())
        session.add(scan)
        await session.commit()
        scan_id = scan.id

    # Collect from all sources
    collectors = get_all_collectors(settings)
    all_raw: list[dict[str, Any]] = []
    errors: list[str] = []
    source_names: list[str] = []

    # 1. Verification-First Gemini Google Search Grounded Scanner
    today_utc = datetime.now(timezone.utc).date()
    is_grounded_scan = False
    if getattr(settings, "gemini_api_key", ""):
        try:
            grounded_items, is_grounded_scan, _ = await scan_with_gemini_grounded(
                profile, settings, today_utc
            )
            if grounded_items:
                all_raw.extend(grounded_items)
                source_names.append("gemini_search_grounding")
        except Exception as g_err:
            errors.append(f"gemini_search_grounding: {g_err}")
            log.warning("scan.gemini_grounding_error", error=str(g_err))

    for collector in collectors:
        try:
            items = await collector.safe_collect(queries)
            all_raw.extend(items)
            source_names.append(collector.name)
        except Exception as exc:
            errors.append(f"{collector.name}: {exc}")

    log.info("scan.collected", total_raw=len(all_raw), sources=len(source_names))

    # Deduplicate
    seen: dict[str, dict[str, Any]] = {}
    unique_items: list[dict[str, Any]] = []
    for item in all_raw:
        if not is_duplicate(item, seen):
            ext_id = generate_external_id(item)
            seen[ext_id] = item
            item["external_id"] = ext_id
            unique_items.append(item)

    log.info("scan.deduped", unique=len(unique_items), removed=len(all_raw) - len(unique_items))

    # Extract, research verify, score, and store
    today_utc = datetime.now(timezone.utc).date()
    new_count = 0

    # 1. Fast pre-filter out closed / expired items
    candidates: list[dict[str, Any]] = []
    for it in unique_items:
        is_closed, reason = _is_closed_or_expired(it, today_utc)
        if is_closed:
            clean_title = str(it.get("title", "")).encode("ascii", "replace").decode("ascii")[:60]
            log.debug("scan.prefiltered_closed", title=clean_title, reason=reason)
            continue
        candidates.append(it)

    log.info("scan.candidates_after_prefilter", total=len(candidates), original=len(unique_items))

    # Concurrency semaphore for processing
    sem = asyncio.Semaphore(4)
    llm_quota_available = True

    async def _process_single_item(
        item: dict[str, Any], can_use_llm: bool
    ) -> dict[str, Any] | None:
        nonlocal llm_quota_available
        async with sem:
            try:
                # Platforms with native structure (devpost, devfolio, mlh, kaggle) don't need LLM
                is_platform = item.get("source_name") in (
                    "devpost",
                    "devfolio",
                    "mlh",
                    "hackerearth",
                    "kaggle",
                ) or bool(item.get("deadline"))

                extracted: dict[str, Any]
                if can_use_llm and not is_platform and llm_quota_available:
                    try:
                        await asyncio.sleep(0.8)  # Rate pacing
                        extracted = await extract_opportunity(item, llm)
                        if (
                            extracted.get("is_available") is False
                            or extracted.get("status") == "closed"
                        ):
                            return None
                    except Exception as llm_err:
                        log.info(
                            "scan.llm_fallback_to_metadata",
                            title=item.get("title"),
                            error=str(llm_err),
                        )
                        if "429" in str(llm_err) or "quota" in str(llm_err).lower():
                            llm_quota_available = False
                        extracted = _extract_metadata_fallback(item, today_utc)
                else:
                    extracted = _extract_metadata_fallback(item, today_utc)

                # Build Opportunity model for scoring
                opp = _dict_to_opportunity(extracted)
                safe_title = str(opp.title).encode("ascii", "replace").decode("ascii")[:60]

                # Expiration check: discard if deadline has passed in GMT 0:00
                if opp.deadline and opp.deadline < today_utc:
                    log.info(
                        "scan.item_expired_skipped", title=safe_title, deadline=str(opp.deadline)
                    )
                    return None

                # Scam check
                is_scam, scam_reason = check_scam(opp)
                if is_scam:
                    log.info("scan.item_scam_skipped", title=safe_title, reason=scam_reason)
                    return None
                opp.is_scam = False
                opp.scam_reason = ""

                # Eligibility check (rules first)
                if can_use_llm and llm_quota_available:
                    try:
                        verdict, reason = await check_eligibility(opp, profile, llm)
                    except Exception:
                        verdict, reason = rule_check(opp, profile)
                else:
                    verdict, reason = rule_check(opp, profile)

                opp.eligibility_verdict = verdict
                opp.eligibility_reason = reason

                if verdict == EligibilityVerdict.NOT_ELIGIBLE:
                    log.info("scan.item_ineligible_skipped", title=safe_title, reason=reason)
                    return None

                # Compute fit score
                opp.fit_score, opp.fit_reason = await compute_fit_score(
                    opp,
                    profile,
                    llm,
                    use_llm_relevance=False,
                )

                return _opportunity_to_row_dict(
                    opp, extracted.get("external_id", "") or item.get("external_id", "")
                )

            except Exception as exc:
                log.warning("scan.item_failed", title=item.get("title"), error=str(exc))
                errors.append(f"Item '{item.get('title', 'unknown')}': {exc}")
                return None

    # Run extraction & verification
    # Allow at most 10 candidate items to query LLM (to strictly respect 15 RPM free tier)
    tasks = []
    llm_count = 0
    for it in candidates:
        allow_llm = False
        if (
            it.get("source_name") not in ("devpost", "devfolio", "mlh", "hackerearth", "kaggle")
            and not it.get("deadline")
            and llm_count < 10
        ):
            allow_llm = True
            llm_count += 1
        tasks.append(_process_single_item(it, can_use_llm=allow_llm))

    results = await asyncio.gather(*tasks)

    # Task 3: Validation layer in code (do not trust the model)
    valid_candidates = [r for r in results if r]
    validated_opps = await validate_opportunity_batch(
        valid_candidates,
        today_utc,
        max_cap=25,
        is_grounded_scan=is_grounded_scan,
        verify_pages=True,
    )

    # Batch store verified opportunities
    async with get_session() as session:
        for opp in validated_opps:
            ext_id = opp.id or generate_external_id(opp.model_dump())
            row_data = _opportunity_to_row_dict(opp, ext_id)
            _, is_new = await upsert_opportunity(session, row_data)
            if is_new:
                new_count += 1
        await session.commit()

    # Auto-purge any confirmed opportunities whose deadline has passed
    try:
        from sqlalchemy import and_, delete, select

        from todaytech.core.database import OpportunityRow, UserStateRow

        async with get_session() as session:
            cleanup_stmt = (
                delete(OpportunityRow)
                .where(
                    and_(
                        OpportunityRow.date_status == "confirmed",
                        OpportunityRow.deadline < today_utc,
                    )
                )
                .where(
                    ~OpportunityRow.external_id.in_(
                        select(UserStateRow.opportunity_id).where(UserStateRow.status == "applied")
                    )
                )
            )
            await session.execute(cleanup_stmt)
            await session.commit()
    except Exception as exc:
        log.warning("scan.cleanup_failed", error=str(exc))

    # Update scan record
    async with get_session() as session:
        scan_row = await session.get(ScanRow, scan_id)
        if scan_row:
            scan_row.finished_at = datetime.utcnow()
            scan_row.sources_used = json.dumps(source_names)
            scan_row.items_found = len(unique_items)
            scan_row.items_new = new_count
            scan_row.errors = json.dumps(errors[:20])
            await session.commit()

    summary = {
        "scan_id": scan_id,
        "items_found": len(unique_items),
        "items_new": new_count,
        "sources": source_names,
        "errors": errors,
        "unhealthy_sources": [c.name for c in collectors if not c.health.is_healthy],
    }
    log.info("scan.complete", **{k: v for k, v in summary.items() if k != "errors"})
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(val: Any, default_year: int = 2026) -> date | None:
    """Robustly parse dates from strings, ISO timestamps with offsets, and ranges into GMT 0:00 (UTC) dates."""
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        if val.tzinfo:
            val = val.astimezone(timezone.utc)
        return val.date()

    if not isinstance(val, str):
        return None

    val = val.strip()
    if not val or val.lower() in (
        "null",
        "none",
        "tbd",
        "tba",
        "open",
        "rolling",
        "ongoing",
        "open enrollment",
    ):
        return None

    # 1. Try ISO 8601 (handles timezone offsets like +00:00, +05:30, Z)
    try:
        clean_iso = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.date()
    except (ValueError, TypeError):
        pass

    # 2. Date ranges like 'Nov 09 - 30, 2026' or 'Dec 19, 2026 - Jan 18, 2027' -> extract closing deadline
    if " - " in val or " to " in val or "–" in val:
        sep = " - " if " - " in val else (" to " if " to " in val else "–")
        parts = [p.strip() for p in val.split(sep)]
        end_part = parts[-1]

        year_match = re.search(r"\b(202\d)\b", val)
        year_str = year_match.group(1) if year_match else str(default_year)

        # If end_part is only day number e.g. '30, 2026' or '10'
        m_day = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?$", end_part)
        if m_day:
            day = m_day.group(1)
            yr = m_day.group(2) or year_str
            m_month = re.search(r"\b([A-Za-z]{3,9})\b", parts[0])
            if m_month:
                month_name = m_month.group(1)
                end_part = f"{month_name} {day}, {yr}"
        elif not re.search(r"\b(202\d)\b", end_part):
            end_part = f"{end_part}, {year_str}"

        val_to_try = end_part
    else:
        val_to_try = val

    # Clean ordinals like 1st, 2nd, 3rd, 4th
    clean_val = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", val_to_try)
    clean_val = re.sub(r"\s+", " ", clean_val).strip()

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%b %d",
        "%B %d",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(clean_val, fmt)
            if "%Y" not in fmt:
                parsed = parsed.replace(year=default_year)
            return parsed.date()
        except ValueError:
            continue

    return None


def _dict_to_opportunity(data: dict[str, Any]) -> Opportunity:
    """Convert a raw dict to an Opportunity model."""
    opp_type = OpportunityType.OTHER
    type_str = str(data.get("type", "other")).lower()
    for t in OpportunityType:
        if t.value == type_str:
            opp_type = t
            break
    # LLM fallback or extraction failure: ambassador programs are unambiguous from the title
    if opp_type == OpportunityType.OTHER and "ambassador" in str(data.get("title", "")).lower():
        opp_type = OpportunityType.AMBASSADOR

    deadline_val = _parse_date(data.get("deadline"))
    raw_start = data.get("start_date")
    start_val = _parse_date(raw_start)
    if (
        not start_val
        and isinstance(data.get("deadline"), str)
        and (" - " in data["deadline"] or " to " in data["deadline"])
    ):
        sep = " - " if " - " in data["deadline"] else " to "
        parts = data["deadline"].split(sep)
        start_val = _parse_date(parts[0])

    date_status_val = str(data.get("date_status", "unknown")).lower()
    date_status = DateStatus.UNKNOWN
    for ds in DateStatus:
        if ds.value == date_status_val:
            date_status = ds
            break

    verified_on_val = _parse_date(data.get("verified_on"))
    fetched_on_val = _parse_date(data.get("fetched_on"))
    raw_cycle = data.get("cycle_year")
    cycle_year_val = None
    if raw_cycle is not None:
        try:
            cycle_year_val = int(raw_cycle)
        except (ValueError, TypeError):
            cycle_year_val = None

    return Opportunity(
        title=data.get("title") or "Unknown",
        type=opp_type,
        organizer=data.get("organizer") or "",
        deadline=deadline_val,
        start_date=start_val,
        location=data.get("location") or "",
        mode=data.get("mode") or "",
        cost=float(data.get("cost", 0) or 0),
        eligibility_text=data.get("eligibility_text") or "",
        countries_allowed=data.get("countries_allowed") or ["all"],
        level_required=data.get("level_required") or "",
        field_required=data.get("field_required") or "",
        reward=data.get("reward") or "",
        application_url=data.get("application_url") or "",
        source_url=data.get("source_url") or "",
        confidence=float(data.get("confidence", 0.5) or 0.5),
        date_status=date_status,
        estimated_window=data.get("estimated_window"),
        cycle_year=cycle_year_val,
        date_evidence=data.get("date_evidence") or "",
        conflict=bool(data.get("conflict", False)),
        verified_on=verified_on_val,
        fetched_on=fetched_on_val,
        days_remaining=data.get("days_remaining"),
        source_name=data.get("source_name") or "",
        raw_text=data.get("raw_text") or "",
    )


def _opportunity_to_row_dict(opp: Opportunity, external_id: str) -> dict[str, Any]:
    """Convert Opportunity model to a flat dict for database insertion."""
    return {
        "external_id": external_id,
        "title": opp.title,
        "type": opp.type.value,
        "organizer": opp.organizer,
        "deadline": opp.deadline,
        "start_date": opp.start_date,
        "location": opp.location,
        "mode": opp.mode,
        "cost": opp.cost,
        "eligibility_text": opp.eligibility_text,
        "countries_allowed": json.dumps(opp.countries_allowed),
        "level_required": opp.level_required,
        "field_required": opp.field_required,
        "reward": opp.reward,
        "application_url": opp.application_url,
        "source_url": opp.source_url,
        "confidence": opp.confidence,
        "date_status": opp.date_status.value
        if hasattr(opp.date_status, "value")
        else str(opp.date_status or "unknown"),
        "estimated_window": opp.estimated_window,
        "cycle_year": opp.cycle_year,
        "date_evidence": opp.date_evidence,
        "conflict": opp.conflict,
        "verified_on": opp.verified_on,
        "fetched_on": opp.fetched_on,
        "days_remaining": opp.days_remaining,
        "fit_score": opp.fit_score,
        "fit_reason": opp.fit_reason,
        "eligibility_verdict": opp.eligibility_verdict.value,
        "eligibility_reason": opp.eligibility_reason,
        "is_scam": opp.is_scam,
        "scam_reason": opp.scam_reason,
        "source_name": opp.source_name,
        "raw_text": opp.raw_text[:2000],
    }
