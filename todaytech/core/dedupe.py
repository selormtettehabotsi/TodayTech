"""Canonical URL normalization and cross-database deduplication engine."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import structlog

from todaytech.core.models import DateStatus

log = structlog.get_logger()

# Tracking parameters to strip
_TRACKING_QUERY_PARAMS = {
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_eid",
    "igshid",
    "feature",
    "spm",
    "tracking",
    "si",
}

# Domains that are social media or third-party aggregators rather than official organiser domains
_NON_OFFICIAL_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "t.me",
    "medium.com",
    "reddit.com",
    "youtube.com",
    "opportunitydesk.org",
    "oyaop.com",
    "youthhubmedia.com",
    "opportunitiesforafricans.com",
    "youthhubafrica.org",
    "jobsearchgh.com",
    "builtin.com",
    "fastweb.com",
    "bold.org",
    "scholarshiptab.com",
    "scholar.africa",
    "bachelorsportal.com",
    "mastersportal.com",
    "findmyorbit.com",
    "acceptedx.com",
    "research.com",
    "ivyscholars.com",
    "sparkadmissions.com",
    "purdueglobal.edu",
    "lanterncollegecounseling.com",
}


def canonicalize_url(url: str | None) -> str:
    """Normalise URLs for deduplication.

    - Lowercase host
    - Drop www.
    - Force https://
    - Strip tracking parameters (utm_*, ref, source, fbclid, etc.)
    - Strip fragments (#...)
    - Strip trailing slashes
    """
    if not url or not isinstance(url, str):
        return ""

    url_str = url.strip()
    if not url_str or url_str.lower() in ("none", "null", "n/a", "#", "undefined"):
        return ""

    if not url_str.lower().startswith(("http://", "https://")):
        url_str = "https://" + url_str

    try:
        parsed = urlparse(url_str)
    except Exception:
        return url_str.lower().rstrip("/")

    # Force https
    scheme = "https"

    # Lowercase host and drop www.
    netloc = parsed.netloc.lower().split(":")[0]  # strip port if present
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Clean path and strip trailing slashes
    path = parsed.path.lower().rstrip("/")

    # Strip tracking parameters from query string
    filtered_query = ""
    if parsed.query:
        keep_params = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            k_lower = k.lower()
            if k_lower.startswith("utm_") or k_lower in _TRACKING_QUERY_PARAMS:
                continue
            keep_params.append((k, v))
        if keep_params:
            keep_params.sort(key=lambda x: x[0])
            filtered_query = urlencode(keep_params)

    return urlunparse((scheme, netloc, path, "", filtered_query, ""))


def is_official_domain(url: str | None, organizer: str = "") -> bool:
    """Return True if url appears to be an official organiser domain rather than an aggregator."""
    if not url:
        return False
    norm_url = canonicalize_url(url)
    try:
        domain = urlparse(norm_url).netloc.lower()
    except Exception:
        return False

    if any(non_off in domain for non_off in _NON_OFFICIAL_DOMAINS):
        return False

    # If organizer is provided, check if part of organizer name is in the domain
    if organizer:
        clean_org = re.sub(r"[^\w]", "", organizer.lower())
        domain_name = domain.split(".")[0]
        if clean_org and (clean_org in domain or domain_name in clean_org):
            return True

    return True


_STOPWORDS = {
    "programme",
    "program",
    "programmes",
    "programs",
    "2024",
    "2025",
    "2026",
    "2027",
    "2028",
    "2029",
    "2030",
    "paid",
    "unpaid",
    "fully",
    "funded",
    "stipend",
    "available",
    "edition",
    "students",
    "student",
    "for",
    "in",
    "at",
    "the",
    "and",
    "of",
    "to",
    "with",
    "by",
    "on",
    "a",
    "an",
    "ghana",
    "ghanaian",
    "nigeria",
    "nigerian",
    "kenya",
    "kenyan",
    "south",
    "africa",
    "african",
    "usa",
    "us",
    "uk",
    "europe",
    "european",
    "global",
    "international",
    "remote",
    "online",
}


def normalize_words(text: str) -> list[str]:
    """Clean text and return significant alphanumeric words, ignoring common stopwords."""
    if not text:
        return []
    # Strip site branding suffix like ' | Youth Hub Media' or ' - Opportunity Desk'
    cleaned = re.sub(
        r"\s*[|–—\-]\s*(?:youth hub|opportunities for|oyaop|scholar africa|bold\.org|fastweb|research\.com|topuniversities|handshake|devpost|devfolio|linkedin|builtin|apply).*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Replace non-alphanumeric characters with spaces
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return [w for w in cleaned.split() if w not in _STOPWORDS and len(w) > 1]


def compute_programme_signature(
    title: str,
    organizer: str = "",
    cycle_year: int | None = None,
) -> tuple[str, str, int]:
    """Extract (norm_organizer, norm_programme, cycle_year) tuple."""
    org_words = normalize_words(organizer)
    title_words = normalize_words(title)

    # If organizer is empty, try to derive organizer from first word(s) of title
    if not org_words and title_words:
        org_str = title_words[0]
        prog_words = title_words[1:] if len(title_words) > 1 else title_words
    else:
        org_str = " ".join(org_words)
        # Strip organizer words from title words to avoid duplicate org tokens in prog name
        prog_words = [w for w in title_words if w not in org_words]
        if not prog_words:
            prog_words = title_words

    norm_prog = " ".join(prog_words)
    # Deduce cycle year if not given
    year = cycle_year
    if year is None:
        yr_match = re.search(r"\b(202\d)\b", title)
        year = int(yr_match.group(1)) if yr_match else 2027

    return org_str, norm_prog, year


def compute_dedupe_key(
    title: str,
    organizer: str = "",
    cycle_year: int | None = None,
    source_url: str = "",
    application_url: str = "",
) -> str:
    """Compute a canonical unique deduplication key.

    Rule:
    (same normalised organisation + programme name [ignoring words like "programme", "program", "2026", "2027", "paid" and country suffixes] + cycle_year)
    OR
    (same normalised official URL).
    """
    org_str, prog_str, year = compute_programme_signature(title, organizer, cycle_year)
    norm_url = canonicalize_url(source_url or application_url)

    # If both organizer and programme name are identifiable, use the programme key
    if org_str and len(prog_str) >= 3:
        key = f"prog:{org_str}:{prog_str}:{year}"
        return key[:255]

    # Fallback to canonical URL
    if norm_url:
        return f"url:{norm_url}"[:255]

    # Fallback to title hash
    import hashlib

    fallback = hashlib.sha256(f"{title}:{organizer}:{year}".encode()).hexdigest()[:16]
    return f"raw:{fallback}"


def opportunity_rank(opp: Any) -> tuple:
    """Compute a comparison tuple for opportunity precedence.

    Higher tuple wins:
    1. date_status: confirmed (4) > estimated (3) > rolling (2) > unknown (1)
    2. official domain source: 1 if official domain, 0 if aggregator/social media
    3. most recently verified (verified_on > fetched_on > first_seen)
    4. completeness: score/fit_score
    """
    # 1. Date status score
    status_raw = getattr(opp, "date_status", None)
    if isinstance(status_raw, DateStatus):
        status_val = status_raw.value
    else:
        status_val = str(status_raw or "unknown").lower()

    status_weights = {
        "confirmed": 4,
        "estimated": 3,
        "rolling": 2,
        "unknown": 1,
    }
    status_score = status_weights.get(status_val, 1)

    # 2. Official domain
    src_url = getattr(opp, "source_url", "") or ""
    org = getattr(opp, "organizer", "") or ""
    is_official = 1 if is_official_domain(src_url, org) else 0

    # 3. Verified recency
    ver_date = getattr(opp, "verified_on", None)
    if isinstance(ver_date, datetime):
        ver_date = ver_date.date()
    elif not isinstance(ver_date, date):
        ver_date = date(1970, 1, 1)

    # 4. Score
    fit_score = getattr(opp, "fit_score", 0) or 0

    return (status_score, is_official, ver_date, fit_score)


def merge_two_opportunity_records(
    winner_data: dict[str, Any], loser_data: dict[str, Any]
) -> dict[str, Any]:
    """Merge two duplicate opportunity records, preserving best fields and winner precedence."""
    merged = dict(winner_data)

    # Prefer winner's official URL if winner is on official domain, else take loser's official URL if present
    winner_url = winner_data.get("source_url") or ""
    loser_url = loser_data.get("source_url") or ""
    if not is_official_domain(winner_url) and is_official_domain(loser_url):
        merged["source_url"] = loser_url
        if not merged.get("application_url"):
            merged["application_url"] = loser_data.get("application_url") or loser_url

    # Retain application URL if missing in winner
    if not merged.get("application_url") and loser_data.get("application_url"):
        merged["application_url"] = loser_data["application_url"]

    # Retain non-empty reward/location/field if missing in winner
    for field in ("reward", "location", "field_required", "level_required", "estimated_window"):
        if not merged.get(field) and loser_data.get(field):
            merged[field] = loser_data[field]

    return merged
