"""Scoring engine — compute fit scores for opportunities.

Weights are configurable in the user's profile.yaml.
Score components: eligibility (40), relevance (25), benefit (20), effort (15).
"""

from __future__ import annotations

from datetime import date

import structlog

from todaytech.core.llm import LLMClient
from todaytech.core.models import EligibilityVerdict, Opportunity, ScoringWeights, UserProfile

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Component scorers (0-100 each, then weighted)
# ---------------------------------------------------------------------------


def _score_eligibility(opp: Opportunity) -> int:
    """Score based on eligibility verdict."""
    match opp.eligibility_verdict:
        case EligibilityVerdict.ELIGIBLE:
            return 100
        case EligibilityVerdict.LIKELY:
            return 75
        case EligibilityVerdict.CHECK:
            return 40
        case EligibilityVerdict.NOT_ELIGIBLE:
            return 0
    return 40


def _score_relevance_heuristic(opp: Opportunity, profile: UserProfile) -> int:
    """Heuristic relevance score based on keyword overlap."""
    score = 0
    title_lower = opp.title.lower()
    elig_lower = opp.eligibility_text.lower()
    combined = f"{title_lower} {elig_lower} {opp.reward.lower()}"

    # Skills match
    skill_matches = sum(1 for s in profile.skills if s.lower() in combined)
    score += min(skill_matches * 15, 45)

    # Interest match
    interest_matches = sum(1 for i in profile.interests if i.lower() in combined)
    score += min(interest_matches * 10, 30)

    # Type preference
    if opp.type.value in profile.opportunity_types:
        score += 15

    # Field match
    if profile.field_of_study.lower() in combined:
        score += 10

    return min(score, 100)


def _score_benefit(opp: Opportunity) -> int:
    """Score the value/benefit of the opportunity."""
    reward_lower = opp.reward.lower()
    score = 30  # base

    high_value_keywords = [
        "full scholarship",
        "fully funded",
        "stipend",
        "paid",
        "prize",
        "award",
        "grant",
        "funding",
        "sponsorship",
        "job offer",
        "full-time",
        "mentorship",
        "tuition",
        "living allowance",
        "airfare",
        "travel",
        "accommodation",
        "housing",
    ]
    medium_value_keywords = [
        "certificate",
        "networking",
        "experience",
        "training",
        "partial",
        "discount",
        "swag",
        "recognition",
        "visa support",
        "health insurance",
        "monthly allowance",
    ]

    for kw in high_value_keywords:
        if kw in reward_lower:
            score += 15

    for kw in medium_value_keywords:
        if kw in reward_lower:
            score += 7

    return min(score, 100)


def _score_effort(opp: Opportunity) -> int:
    """Score effort/deadline feasibility (higher = easier to apply)."""
    score = 50  # base

    # Deadline proximity
    if opp.deadline:
        days_left = (opp.deadline - date.today()).days
        if days_left < 0:
            return 0  # expired
        elif days_left <= 3:
            score -= 30  # very tight
        elif days_left <= 7:
            score -= 15
        elif days_left <= 30:
            score += 10
        else:
            score += 20

    # Cost
    if opp.cost == 0:
        score += 20
    elif opp.cost > 50:
        score -= 20

    # Online vs in-person
    if opp.mode and "online" in opp.mode.lower():
        score += 10

    return max(0, min(score, 100))


# ---------------------------------------------------------------------------
# LLM-enhanced relevance (optional, for top candidates)
# ---------------------------------------------------------------------------

_RELEVANCE_PROMPT = """Rate how relevant this opportunity is for this user (0-100).

OPPORTUNITY:
- Title: {title}
- Type: {type}
- Organizer: {organizer}
- Reward: {reward}
- Eligibility: {eligibility_text}

USER PROFILE:
- Field: {field_of_study}
- Skills: {skills}
- Interests: {interests}
- Causes: {causes}
- Level: {level}

Respond in JSON:
{{"score": <0-100>, "reason": "one sentence"}}
"""


async def _llm_relevance(
    opp: Opportunity,
    profile: UserProfile,
    llm: LLMClient,
) -> tuple[int, str]:
    """Get LLM-assessed relevance score."""
    try:
        data = await llm.generate_json(
            _RELEVANCE_PROMPT.format(
                title=opp.title,
                type=opp.type.value,
                organizer=opp.organizer,
                reward=opp.reward or "Not stated",
                eligibility_text=opp.eligibility_text[:300] or "Not stated",
                field_of_study=profile.field_of_study,
                skills=", ".join(profile.skills[:10]),
                interests=", ".join(profile.interests[:5]),
                causes=", ".join(profile.causes[:3]),
                level=profile.level,
            ),
            system="You are an opportunity relevance scorer.",
        )
        return int(data.get("score", 50)), data.get("reason", "")
    except Exception as exc:
        log.warning("scoring.llm_relevance_failed", error=str(exc))
        return _score_relevance_heuristic(opp, profile), "Heuristic score (LLM unavailable)"


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------


async def compute_fit_score(
    opp: Opportunity,
    profile: UserProfile,
    llm: LLMClient,
    *,
    use_llm_relevance: bool = True,
    weights: ScoringWeights | None = None,
) -> tuple[int, str]:
    """Compute weighted fit score (0-100) and a one-line reason.

    Components:
    - Eligibility (default 40%): based on eligibility verdict
    - Relevance (default 25%): skills/interests match
    - Benefit (default 20%): reward value
    - Effort (default 15%): deadline feasibility, cost
    """
    w = weights or profile.scoring_weights

    elig_score = _score_eligibility(opp)
    benefit_score = _score_benefit(opp)
    effort_score = _score_effort(opp)

    # Relevance: LLM if enabled, heuristic otherwise
    if use_llm_relevance:
        relevance_score, relevance_reason = await _llm_relevance(opp, profile, llm)
    else:
        relevance_score = _score_relevance_heuristic(opp, profile)
        relevance_reason = ""

    total = (
        elig_score * w.eligibility
        + relevance_score * w.relevance
        + benefit_score * w.benefit
        + effort_score * w.effort
    ) // 100

    # Build reason
    parts = []
    if relevance_reason:
        parts.append(relevance_reason)
    elif relevance_score >= 60:
        parts.append("Good match for your skills and interests")
    if benefit_score >= 60:
        parts.append("strong rewards")
    if effort_score >= 70:
        parts.append("feasible timeline")
    elif effort_score <= 30:
        parts.append("tight deadline")

    reason = "; ".join(parts) if parts else "General match"

    return min(total, 100), reason
