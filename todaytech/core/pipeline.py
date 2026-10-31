"""Intelligence pipeline — collect → normalize → dedupe → extract → score → store.

This is the central orchestrator that runs a full scan cycle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog
from thefuzz import fuzz

from todaytech.collectors import get_all_collectors
from todaytech.core.config import get_settings, load_profile
from todaytech.core.database import (
    ScanRow,
    get_last_scan,
    get_session,
    upsert_opportunity,
)
from todaytech.core.eligibility import check_eligibility
from todaytech.core.llm import LLMClient, create_llm_client_from_settings
from todaytech.core.models import EligibilityVerdict, Opportunity, OpportunityType, UserProfile
from todaytech.core.scam_filter import check_scam
from todaytech.core.scoring import compute_fit_score

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
        queries.extend([
            f"Eiffel Excellence Scholarship 2027 {field} application",
            f"Campus France scholarship 2027 {country} STEM",
            f"Charpak scholarship France {country} 2027 Masters",
            f"Erasmus Mundus Joint Master {field} 2027 application",
            f"Mastercard Foundation scholarship Europe 2027 Africa",
            f"Mastercard Foundation Sciences Po 2027 Africa",
            f"study in France scholarship 2027 {field} {country}",
        ])

        # Broader Europe
        queries.extend([
            f"DAAD scholarship {country} 2027 {field}",
            f"Chevening scholarship {country} 2027 application",
            f"Commonwealth scholarship {country} 2027 Masters {field}",
            f"Netherlands Fellowship Programme {country} 2027 STEM",
            f"Swiss Government Excellence Scholarship {country} 2027",
            f"Europe scholarship {country} students {field} 2027",
        ])

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
            queries.extend([
                f"site:linkedin.com scholarship Europe France Africa {field} 2027",
                f"site:linkedin.com Mastercard Foundation Scholars 2027 application",
                f"site:linkedin.com Eiffel scholarship Erasmus Mundus 2027 Africa",
            ])
        if "fellowship" in types:
            queries.append(f"site:linkedin.com cloud DevOps fellowship Africa Europe 2027")

    if "fellowship" in types:
        queries.append(f"site:linkedin.com software engineering fellowship 2027 apply")
    if "internship" in types:
        queries.append(f"site:linkedin.com AWS cloud DevOps scholarship internship 2027")

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


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """Extract structured opportunity data from this raw text.

RAW TEXT:
{raw_text}

TITLE: {title}
SOURCE URL: {source_url}

Return JSON with these fields (use null for unknown):
{{
  "title": "string",
  "type": "hackathon|internship|scholarship|fellowship|grant|program|competition|ambassador|other",
  "organizer": "string",
  "deadline": "YYYY-MM-DD or null",
  "start_date": "YYYY-MM-DD or null",
  "location": "string",
  "mode": "online|in-person|hybrid",
  "cost": 0,
  "eligibility_text": "string",
  "countries_allowed": ["list", "of", "countries"] or [],
  "level_required": "string or empty",
  "field_required": "string or empty",
  "reward": "string describing prizes/benefits",
  "application_url": "string",
  "confidence": 0.0 to 1.0
}}

RULES:
- Use type "ambassador" for campus/student ambassador, student champion, community lead, or
  student developer advocate programs where students represent an organization on campus.
- Never invent deadlines. If unsure, use null.
- Extract actual data from the text; do not hallucinate.
- confidence: 0.9+ if clearly an opportunity, 0.5-0.9 if uncertain, <0.5 if probably not an opportunity.
"""


async def extract_opportunity(
    item: dict[str, Any],
    llm: LLMClient,
) -> dict[str, Any]:
    """Use LLM to extract structured fields from raw opportunity data."""
    try:
        data = await llm.generate_json(
            _EXTRACT_PROMPT.format(
                raw_text=item.get("raw_text", "")[:1000],
                title=item.get("title", ""),
                source_url=item.get("source_url", ""),
            ),
            system="You are a data extraction engine for student opportunities.",
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

    # Extract, score, and store
    new_count = 0
    for item in unique_items:
        try:
            # LLM extraction
            extracted = await extract_opportunity(item, llm)

            # Build Opportunity model for scoring
            opp = _dict_to_opportunity(extracted)

            # Scam check
            is_scam, scam_reason = check_scam(opp)
            opp.is_scam = is_scam
            opp.scam_reason = scam_reason

            # Eligibility check
            verdict, reason = await check_eligibility(opp, profile, llm)
            opp.eligibility_verdict = verdict
            opp.eligibility_reason = reason

            # Skip scoring for ineligible
            if verdict != EligibilityVerdict.NOT_ELIGIBLE:
                opp.fit_score, opp.fit_reason = await compute_fit_score(
                    opp,
                    profile,
                    llm,
                    use_llm_relevance=False,  # heuristic for speed
                )

            # Store
            async with get_session() as session:
                row_data = _opportunity_to_row_dict(opp, extracted.get("external_id", ""))
                _, is_new = await upsert_opportunity(session, row_data)
                await session.commit()
                if is_new:
                    new_count += 1

        except Exception as exc:
            log.warning("scan.item_failed", title=item.get("title"), error=str(exc))
            errors.append(f"Item '{item.get('title', 'unknown')}': {exc}")

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


def _parse_date(val: Any) -> date | None:
    """Try to parse a date from various formats."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%B %d, %Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(val.strip(), fmt).date()
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

    return Opportunity(
        title=data.get("title", "Unknown"),
        type=opp_type,
        organizer=data.get("organizer", ""),
        deadline=_parse_date(data.get("deadline")),
        start_date=_parse_date(data.get("start_date")),
        location=data.get("location", ""),
        mode=data.get("mode", ""),
        cost=float(data.get("cost", 0) or 0),
        eligibility_text=data.get("eligibility_text", ""),
        countries_allowed=data.get("countries_allowed", []) or [],
        level_required=data.get("level_required", ""),
        field_required=data.get("field_required", ""),
        reward=data.get("reward", ""),
        application_url=data.get("application_url", ""),
        source_url=data.get("source_url", ""),
        confidence=float(data.get("confidence", 0.5) or 0.5),
        source_name=data.get("source_name", ""),
        raw_text=data.get("raw_text", ""),
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
        "fit_score": opp.fit_score,
        "fit_reason": opp.fit_reason,
        "eligibility_verdict": opp.eligibility_verdict.value,
        "eligibility_reason": opp.eligibility_reason,
        "is_scam": opp.is_scam,
        "scam_reason": opp.scam_reason,
        "source_name": opp.source_name,
        "raw_text": opp.raw_text[:2000],
    }
