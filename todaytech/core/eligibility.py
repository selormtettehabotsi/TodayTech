"""Eligibility engine — rule-based checks first, LLM for ambiguous cases.

The engine never says "Eligible" when confidence is low; it falls back to
"Check" with a reason explaining what the user should verify.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from todaytech.core.llm import LLMClient
from todaytech.core.models import EligibilityVerdict, Opportunity, UserProfile

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Rule-based checks
# ---------------------------------------------------------------------------


def _check_deadline(opp: Opportunity) -> tuple[EligibilityVerdict, str] | None:
    """If the deadline has passed in GMT 0:00, not eligible."""
    today = datetime.now(timezone.utc).date()
    if opp.deadline and opp.deadline < today:
        return EligibilityVerdict.NOT_ELIGIBLE, f"Deadline passed on {opp.deadline} (GMT 0:00)"
    return None


def _check_country(opp: Opportunity, profile: UserProfile) -> tuple[EligibilityVerdict, str] | None:
    """Check country restrictions."""
    if not opp.countries_allowed:
        return None  # No restriction stated
    allowed = [c.lower().strip() for c in opp.countries_allowed]
    if "all" in allowed or "global" in allowed:
        return None
    user_countries = [profile.country.lower()] + [c.lower() for c in profile.citizenship]
    if any(c in allowed for c in user_countries):
        return None
    return EligibilityVerdict.NOT_ELIGIBLE, (
        f"Restricted to {', '.join(opp.countries_allowed)}; "
        f"your country/citizenship: {profile.country}"
    )


def _check_level(opp: Opportunity, profile: UserProfile) -> tuple[EligibilityVerdict, str] | None:
    """Check education level requirement."""
    if not opp.level_required:
        return None
    req = opp.level_required.lower()
    user = profile.level.lower()
    # Simple hierarchy
    levels = ["self-taught", "bootcamp", "undergraduate", "masters", "phd", "graduated"]
    if req in levels and user in levels:
        if levels.index(user) < levels.index(req):
            return EligibilityVerdict.NOT_ELIGIBLE, (
                f"Requires {opp.level_required} level; you are {profile.level}"
            )
    return None


def _check_field(opp: Opportunity, profile: UserProfile) -> tuple[EligibilityVerdict, str] | None:
    """Check field of study requirement."""
    if not opp.field_required:
        return None
    required_lower = opp.field_required.lower()
    user_field = profile.field_of_study.lower()
    # If user's field is mentioned in the requirement, likely ok
    if user_field in required_lower or required_lower in user_field:
        return None
    # Check for broad STEM match
    stem_fields = {
        "computer science",
        "engineering",
        "technology",
        "science",
        "stem",
        "ict",
        "mathematics",
        "digital",
        "software",
        "data",
        "informatics",
        "computing",
        "cybersecurity",
    }
    if any(s in required_lower for s in stem_fields) and any(s in user_field for s in stem_fields):
        return None
    return EligibilityVerdict.CHECK, (
        f"Requires '{opp.field_required}'; your field is '{profile.field_of_study}' — verify eligibility"
    )


def _check_fee(opp: Opportunity, profile: UserProfile) -> tuple[EligibilityVerdict, str] | None:
    """Check application fee."""
    if opp.cost > 0 and opp.cost > profile.max_application_fee:
        return EligibilityVerdict.NOT_ELIGIBLE, (
            f"Application fee ${opp.cost:.0f} exceeds your max ${profile.max_application_fee:.0f}"
        )
    return None


def rule_check(opp: Opportunity, profile: UserProfile) -> tuple[EligibilityVerdict, str]:
    """Run all rule-based checks.  Returns the first failing check or Eligible."""
    checks = [
        _check_deadline(opp),
        _check_country(opp, profile),
        _check_level(opp, profile),
        _check_field(opp, profile),
        _check_fee(opp, profile),
    ]

    for result in checks:
        if result is not None:
            return result

    return EligibilityVerdict.ELIGIBLE, "Passes all rule-based eligibility checks"


# ---------------------------------------------------------------------------
# LLM-assisted eligibility (for vague text)
# ---------------------------------------------------------------------------

_ELIGIBILITY_PROMPT = """You are an eligibility checker for student/early-career opportunities.

Given the opportunity details and the user profile below, determine eligibility.

OPPORTUNITY:
- Title: {title}
- Eligibility text: {eligibility_text}
- Countries allowed: {countries}
- Level required: {level_required}
- Field required: {field_required}

USER PROFILE:
- Country: {country}, Citizenship: {citizenship}
- Level: {level}, Year: {year}
- Field: {field_of_study}
- Skills: {skills}

Respond in JSON:
{{
  "verdict": "Eligible" | "Likely" | "Check" | "Not eligible",
  "reason": "one-line explanation"
}}

Rules:
- Use "Eligible" only if clearly meets all stated requirements.
- Use "Likely" if requirements are vague but profile is a good fit.
- Use "Check" if important details are unclear or missing.
- Use "Not eligible" only if clearly disqualified.
- Never guess; when uncertain, use "Check".
"""


async def llm_eligibility_check(
    opp: Opportunity,
    profile: UserProfile,
    llm: LLMClient,
) -> tuple[EligibilityVerdict, str]:
    """Use LLM to assess eligibility for opportunities with vague criteria."""
    prompt = _ELIGIBILITY_PROMPT.format(
        title=opp.title,
        eligibility_text=opp.eligibility_text or "Not stated",
        countries=", ".join(opp.countries_allowed) or "Not stated",
        level_required=opp.level_required or "Not stated",
        field_required=opp.field_required or "Not stated",
        country=profile.country,
        citizenship=", ".join(profile.citizenship) or profile.country,
        level=profile.level,
        year=profile.year,
        field_of_study=profile.field_of_study,
        skills=", ".join(profile.skills[:10]),
    )

    try:
        data = await llm.generate_json(prompt, system="You are an eligibility assessment engine.")
        verdict_str = data.get("verdict", "Check")
        reason = data.get("reason", "Unable to determine eligibility")

        try:
            verdict = EligibilityVerdict(verdict_str)
        except ValueError:
            verdict = EligibilityVerdict.CHECK

        return verdict, reason

    except Exception as exc:
        log.warning("eligibility.llm_failed", error=str(exc))
        return EligibilityVerdict.CHECK, "Could not verify eligibility — check manually"


# ---------------------------------------------------------------------------
# Combined check
# ---------------------------------------------------------------------------


async def check_eligibility(
    opp: Opportunity,
    profile: UserProfile,
    llm: LLMClient,
) -> tuple[EligibilityVerdict, str]:
    """Full eligibility check: rules first, then LLM for ambiguous cases."""
    verdict, reason = rule_check(opp, profile)

    # If rules give a definitive answer, use it
    if verdict in (EligibilityVerdict.ELIGIBLE, EligibilityVerdict.NOT_ELIGIBLE):
        # If eligible by rules but has vague eligibility text, double-check
        if (
            verdict == EligibilityVerdict.ELIGIBLE
            and opp.eligibility_text
            and len(opp.eligibility_text) > 50
        ):
            llm_verdict, llm_reason = await llm_eligibility_check(opp, profile, llm)
            if llm_verdict == EligibilityVerdict.NOT_ELIGIBLE:
                return llm_verdict, llm_reason
            if llm_verdict == EligibilityVerdict.CHECK:
                return llm_verdict, llm_reason
        return verdict, reason

    # CHECK from rules — ask LLM for more nuance
    if verdict == EligibilityVerdict.CHECK and opp.eligibility_text:
        return await llm_eligibility_check(opp, profile, llm)

    return verdict, reason
