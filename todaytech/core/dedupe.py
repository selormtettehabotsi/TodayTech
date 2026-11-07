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

# Domains that are social media, blogs, job boards, or third-party aggregators/directories
# rather than an organiser's official page.
_NON_OFFICIAL_DOMAINS = {
    # Social media & messaging platforms
    "facebook.com",
    "fb.com",
    "linkedin.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "t.me",
    "telegram.me",
    "telegram.org",
    "reddit.com",
    "youtube.com",
    "tiktok.com",
    "quora.com",
    "pinterest.com",
    # Aggregators, job/scholarship boards & lead directories
    "devpost.com",
    "dev.events",
    "research.com",
    "scholarshipsads.com",
    "scholarshiptab.com",
    "opportunitiesforafricans.com",
    "opportunitydesk.org",
    "oyaop.com",
    "youthop.com",
    "youthopportunitieshub.com",
    "youthhubmedia.com",
    "youthhubafrica.org",
    "jobsearchgh.com",
    "builtin.com",
    "fastweb.com",
    "bold.org",
    "scholar.africa",
    "bachelorsportal.com",
    "mastersportal.com",
    "findmyorbit.com",
    "acceptedx.com",
    "ivyscholars.com",
    "sparkadmissions.com",
    "purdueglobal.edu",
    "lanterncollegecounseling.com",
    "advance-africa.com",
    "thescholarshipwhizz.org",
    "launchedglobal.in",
    "afterschoolafrica.com",
    "scholars4dev.com",
    "internships.com",
    "chegg.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "handshake.com",
    "untapped.io",
    "wayup.com",
    "unstop.com",
    "hackerearth.com",
    "eventbrite.com",
    "meetup.com",
    "scholarshipbob.com",
    "scholarships.jobzhiring.com",
    "scholarshipregion.com",
    "globalscholarships.com",
    "grabascholarship.com",
    "indiatoday.in",
    "scholarshipstory.com",
    "wemakescholars.com",
    "heysuccess.com",
    "armacad.info",
    "fundsforngos.org",
    "opportunitiescircle.com",
    "opportunitiescorner.com",
    "fullyfundedaffairs.com",
    # Blogs, newsletters & self-publishing platforms
    "medium.com",
    "substack.com",
    "wordpress.com",
    "blogspot.com",
    "blogger.com",
    "tumblr.com",
    "hashnode.dev",
    "dev.to",
    "wixsite.com",
    "weebly.com",
    "notion.site",
    "canva.site",
    "site123.me",
    "linktr.ee",
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
    """Return True if url appears to be an official organiser domain rather than an aggregator/blog/social site."""
    if not url or not isinstance(url, str):
        return False
    norm_url = canonicalize_url(url)
    if not norm_url:
        return False
    try:
        parsed = urlparse(norm_url)
        domain = (parsed.hostname or parsed.netloc or "").lower().split(":")[0]
    except Exception:
        return False

    if not domain:
        return False

    # 1. Reject known non-official aggregator, blog, social media, and directory domains
    for non_off in _NON_OFFICIAL_DOMAINS:
        if domain == non_off or domain.endswith("." + non_off):
            return False

    # 2. GitHub: Only official GitHub domains (e.g. education.github.com) or official repos are accepted
    if domain == "github.com" or domain.endswith(".github.com"):
        if domain in ("education.github.com", "accelerator.github.com"):
            return True
        clean_org = organizer.lower() if organizer else ""
        if "github" in clean_org:
            path = parsed.path.lower()
            curated_markers = (
                "curated",
                "list",
                "awesome",
                "collection",
                "directory",
                "everything",
                "programs",
                "scholarships",
                "internships",
            )
            if any(marker in path for marker in curated_markers):
                return False
            return True
        # If GitHub is not the organiser, any repo is a third-party project or curated list
        return False

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


_GENERIC_ORGANIZERS = {
    "",
    "none",
    "null",
    "unknown",
    "n/a",
    "independent",
    "various",
    "multiple",
    "other",
    "campus",
    "student",
    "students",
    "tech",
    "internship",
    "internships",
    "hackathon",
    "hackathons",
    "scholarship",
    "scholarships",
    "fellowship",
    "fellowships",
    "ambassador",
    "ambassadors",
    "program",
    "programme",
    "programs",
    "programmes",
    "global",
    "remote",
    "online",
    "annual",
    "university",
    "college",
}


def derive_organizer_from_official_domain(url: str | None) -> str:
    """Derive clean organiser name from an official domain, or return '' if impossible.

    Task 3: Never merge items whose organiser is blank or generic.
    If missing, derive it from the official domain; if impossible, do not merge.
    """
    if not url or not is_official_domain(url):
        return ""
    try:
        norm_url = canonicalize_url(url)
        parsed = urlparse(norm_url)
        hostname = (parsed.hostname or parsed.netloc or "").lower().split(":")[0]
    except Exception:
        return ""
    if not hostname:
        return ""

    parts = hostname.split(".")
    tlds = {
        "org",
        "com",
        "edu",
        "gov",
        "io",
        "net",
        "in",
        "gh",
        "uk",
        "ca",
        "ai",
        "co",
        "de",
        "fr",
        "int",
        "eu",
        "info",
        "biz",
        "me",
        "tech",
    }
    non_tld_parts = [p for p in parts if p not in tlds]
    if not non_tld_parts:
        return ""

    # E.g. for cap.giftoflife.org -> non_tld_parts = ["cap", "giftoflife"] -> main is giftoflife
    main_token = non_tld_parts[-1]
    if main_token in _GENERIC_ORGANIZERS and len(non_tld_parts) > 1:
        main_token = non_tld_parts[-2]

    # Special handling for e.g. withgoogle -> google
    if main_token.endswith("google"):
        main_token = "google"

    clean = re.sub(r"[^\w]", "", main_token.lower())
    if clean and clean not in _GENERIC_ORGANIZERS and len(clean) >= 3:
        return clean
    return ""


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


def is_valid_estimated_opportunity(opp: Any) -> bool:
    """Return True if the opportunity meets all criteria to be shown as estimated.

    Task 4 rules:
    - Official page exists (is_official_domain is True and source_url is non-empty)
    - Evidence of a previous cycle exists (prev_cycle_date, prev_cycle_source, or descriptive evidence)
    - Items with only 'Expected window TBA' and no evidence must not be shown.
    """
    src_url = getattr(opp, "source_url", "") or ""
    org = getattr(opp, "organizer", "") or ""
    if not src_url or not is_official_domain(src_url, org):
        return False

    status_raw = getattr(opp, "date_status", "")
    status = status_raw.value if hasattr(status_raw, "value") else str(status_raw or "").lower()
    if status != "estimated":
        return False

    prev_date = getattr(opp, "prev_cycle_date", None)
    prev_src = (getattr(opp, "prev_cycle_source", "") or "").strip()
    date_ev = (getattr(opp, "date_evidence", "") or "").strip()
    window = (getattr(opp, "estimated_window", "") or "").strip()

    clean_win = window.lower()
    is_tba_window = not clean_win or clean_win in (
        "expected window tba",
        "window tba",
        "tba",
        "none",
        "null",
        "to be announced",
    )

    has_prev_cycle_evidence = bool(
        prev_date
        or (prev_src and is_official_domain(prev_src, org))
        or re.search(
            r"\b(?:202\d|cycle|previous|past|last\s+year|cohort|archive)\b", date_ev, re.IGNORECASE
        )
        or re.search(r"\bbased on (?:the )?202\d\b", window, re.IGNORECASE)
    )

    if is_tba_window and not (prev_date or prev_src or date_ev):
        return False

    return has_prev_cycle_evidence


def compute_programme_signature(
    title: str,
    organizer: str = "",
    cycle_year: int | None = None,
    source_url: str = "",
) -> tuple[str, str, int]:
    """Extract (norm_organizer, norm_programme, cycle_year) tuple.

    Task 3: Never merge items whose organiser is blank or generic.
    If missing, derive it from the official domain; if impossible, do not merge.
    """
    org_clean = re.sub(r"[^\w\s]", "", (organizer or "").lower()).strip()
    is_generic = (
        not org_clean
        or org_clean in _GENERIC_ORGANIZERS
        or all(w in _GENERIC_ORGANIZERS for w in org_clean.split())
    )

    if is_generic:
        # Try to derive from official domain; if impossible, do not guess from title
        derived = derive_organizer_from_official_domain(source_url)
        org_str = derived if derived else ""
    else:
        org_words = normalize_words(organizer)
        org_str = " ".join(org_words)

    title_words = normalize_words(title)
    if org_str:
        prog_words = [w for w in title_words if w != org_str and w not in org_str.split()]
        if not prog_words:
            prog_words = title_words
    else:
        prog_words = title_words

    norm_prog = " ".join(prog_words)
    # Deduce cycle year if not given
    year = cycle_year
    if year is None:
        yr_match = re.search(r"\b(202\d)\b", title)
        year = int(yr_match.group(1)) if yr_match else 2026

    return org_str, norm_prog, year


def compute_dedupe_key(
    title: str,
    organizer: str = "",
    cycle_year: int | None = None,
    source_url: str = "",
    application_url: str = "",
) -> str:
    """Compute a canonical unique deduplication key.

    Rule (Task 3):
    - Never merge items whose organiser is blank or generic.
    - If missing, derive it from the official domain; if impossible, do not merge.
    - Only merge if both valid non-generic organiser and programme name are established.
    - If impossible, keep item separate (keyed by its canonical URL or unique hash).
    """
    clean_src = source_url or application_url
    org_str, prog_str, year = compute_programme_signature(
        title, organizer, cycle_year, source_url=clean_src
    )
    norm_url = canonicalize_url(clean_src)

    # Only merge by programme key if organiser is valid and not generic
    if org_str and len(prog_str) >= 3:
        key = f"prog:{org_str}:{prog_str}:{year}"
        return key[:255]

    # If organiser could not be determined from official domain: do NOT merge!
    if norm_url:
        return f"url:{norm_url}"[:255]

    # Fallback to unique hash
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
) -> dict[str, Any] | None:
    """Merge two duplicate opportunity records, preserving best fields and winner precedence.

    Rules (Task 3):
    - Never let a non-official URL replace an official one when merging.
    - If no merged record has an official URL, drop the item (return None).
    """
    merged = dict(winner_data)

    winner_url = winner_data.get("source_url") or ""
    loser_url = loser_data.get("source_url") or ""
    winner_org = winner_data.get("organizer") or ""
    loser_org = loser_data.get("organizer") or ""

    winner_is_off = is_official_domain(winner_url, winner_org)
    loser_is_off = is_official_domain(loser_url, loser_org)

    # Official URL precedence: never let non-official replace official!
    if winner_is_off:
        merged["source_url"] = winner_url
    elif loser_is_off:
        merged["source_url"] = loser_url
    else:
        # Check application_url
        winner_app = winner_data.get("application_url") or ""
        loser_app = loser_data.get("application_url") or ""
        if is_official_domain(winner_app, winner_org):
            merged["source_url"] = winner_app
        elif is_official_domain(loser_app, loser_org):
            merged["source_url"] = loser_app
        else:
            # Neither record has an official URL -> drop the item!
            return None

    # Application URL: similarly prefer official URL
    winner_app = winner_data.get("application_url") or ""
    loser_app = loser_data.get("application_url") or ""
    if winner_app and is_official_domain(winner_app, winner_org):
        merged["application_url"] = winner_app
    elif loser_app and is_official_domain(loser_app, loser_org):
        merged["application_url"] = loser_app
    elif winner_app:
        merged["application_url"] = winner_app
    elif loser_app:
        merged["application_url"] = loser_app

    # Retain non-empty reward/location/field if missing in winner
    for field in ("reward", "location", "field_required", "level_required", "estimated_window"):
        if not merged.get(field) and loser_data.get(field):
            merged[field] = loser_data[field]

    return merged
