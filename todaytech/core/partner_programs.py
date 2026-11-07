"""Validation for programmes delivered through partner institutions (e.g. Mastercard Foundation Scholars)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import structlog

from todaytech.core.dedupe import is_official_domain

log = structlog.get_logger()

# Regexp identifying partner-delivered consortium/foundation programmes
_PARTNER_PROGRAM_PATTERNS = [
    r"\bmastercard\s+foundation\b",
]

# Known partner institutions and their domains
_PARTNER_INSTITUTIONS = [
    ("knust", ["knust.edu.gh"]),
    ("kwame nkrumah", ["knust.edu.gh"]),
    ("ashesi", ["ashesi.edu.gh"]),
    ("sciences po", ["sciencespo.fr"]),
    ("edinburgh", ["ed.ac.uk"]),
    ("cambridge", ["cam.ac.uk"]),
    ("oxford", ["ox.ac.uk"]),
    ("toronto", ["utoronto.ca"]),
    ("mcgill", ["mcgill.ca"]),
    ("arizona state", ["asu.edu"]),
    ("asu", ["asu.edu"]),
    ("cmu africa", ["cmu.edu"]),
    ("carnegie mellon", ["cmu.edu"]),
    ("makerere", ["mak.ac.ug"]),
    ("pretoria", ["up.ac.za"]),
    ("cape town", ["uct.ac.za"]),
    ("american university of beirut", ["aub.edu.lb"]),
    ("aub", ["aub.edu.lb"]),
    ("american university of cairo", ["aucegypt.edu"]),
    ("auc", ["aucegypt.edu"]),
]

_GRADUATE_ONLY_KEYWORDS = [
    "graduate study",
    "postgraduate",
    "masters",
    "master's",
    "phd",
    "doctoral",
]


def validate_partner_program(
    title: str,
    organizer: str = "",
    source_url: str = "",
    application_url: str = "",
    level_required: str = "",
) -> tuple[bool, str]:
    """Validate that a partner-delivered programme meets all partner requirements.

    Task 5 rules:
    - For programmes delivered through partner institutions (e.g. Mastercard Foundation Scholars),
      require the specific partner institution, that institution's official page, and its deadline,
      plus an eligibility check that a Ghanaian undergraduate qualifies there; otherwise exclude.

    Returns (is_valid, failure_reason).
    """
    combined_text = f"{title} {organizer}".lower()

    is_partner = any(
        re.search(pat, combined_text, re.IGNORECASE) for pat in _PARTNER_PROGRAM_PATTERNS
    )
    if not is_partner:
        return True, ""

    # 1. Partner institution requirement: Must specify the specific partner institution
    matched_institution = None
    expected_domains: list[str] = []
    for inst_name, domains in _PARTNER_INSTITUTIONS:
        if re.search(rf"\b{re.escape(inst_name)}\b", combined_text, re.IGNORECASE):
            matched_institution = inst_name
            expected_domains = domains
            break

    if not matched_institution:
        return (
            False,
            "Partner-delivered programme does not name a specific partner institution",
        )

    # 2. Institution official page requirement:
    # source_url or application_url must be on the partner institution's domain,
    # NOT generic foundation homepages (e.g. mastercardfdn.org) or aggregators.
    urls_to_check = [u for u in (source_url, application_url) if u]
    if not urls_to_check:
        return False, "Partner-delivered programme missing institution official page"

    valid_domain = False
    for u in urls_to_check:
        try:
            parsed = urlparse(u)
            hostname = (parsed.hostname or "").lower()
            if any(hostname == exp or hostname.endswith("." + exp) for exp in expected_domains) or (
                is_official_domain(u, matched_institution) and "mastercardfdn.org" not in hostname
            ):
                valid_domain = True
                break
        except Exception:
            continue

    if not valid_domain:
        return (
            False,
            f"Partner-delivered programme source URL is not {matched_institution}'s official page",
        )

    # 3. Eligibility check: A Ghanaian undergraduate must qualify there.
    # Exclude graduate-only programmes (e.g. Sciences Po Masters, Cambridge Masters)
    combined_level_text = f"{title} {level_required}".lower()
    if any(
        re.search(rf"\b{re.escape(kw)}\b", combined_level_text) for kw in _GRADUATE_ONLY_KEYWORDS
    ):
        if "undergraduate" not in combined_level_text and "bachelor" not in combined_level_text:
            return (
                False,
                f"{matched_institution} partner programme is graduate/postgraduate only; excludes undergraduates",
            )

    return True, ""
