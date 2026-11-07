"""Detection and rejection of roundup pages, listicles, directories, and finders.

Requires each opportunity to be a single programme with one organiser and one application page.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Regex patterns for listicles, directories, finders, and generic roundups
# ---------------------------------------------------------------------------

_ROUNDUP_TITLE_PATTERNS = [
    # 1. Number at start followed by words like fully funded, best, top, or scholarships (case-insensitive)
    r"^\d{1,4}\+?\s+(?:fully\s+funded|best|top|scholarships|internships|fellowships|grants|hackathons|opportunities|programs|programmes|colleges|universities|ways|tips)\b",
    # 2. "Fully Funded ... for [nationality] students"
    r"\bfully\s+funded\b.*?\bfor\s+[a-zA-Z\s]+\s+students\b",
    # 3. Titles containing "curated list"
    r"\bcurated\s+list\b",
    # 4. Titles containing "finder"
    r"\bfinder\b",
    # 5. Titles containing "directory"
    r"\bdirectory\b",
    # 6. Titles containing "degree programs"
    r"\bdegree\s+programs?\b",
    # 7. GitHub repository names (e.g. owner/repo, Julian048/CS-Everything-but-Internships)
    r"\b[a-zA-Z0-9_\-\.]+\/[a-zA-Z0-9_\-\.]+\b",
    # Listicles anywhere: "27 Scholarships for Computer Sciences in Ghana", "40 Best Internships...", "10 Top Hackathons", "75 Computer Science Scholarships"
    r"\b\d{1,3}\+?\s+(?:best|top|scholarships|internships|fellowships|grants|hackathons|opportunities|programs|programmes|colleges|universities|ways|tips)\b",
    # "Find 40 Best...", "Find 10 Top..."
    r"\bfind\s+\d{1,3}\+?\s+(?:best|top)\b",
    # "The Best Hackathons for...", "Best Computer Science Scholarships 2026", "Top Tech Internships"
    r"\b(?:the\s+)?(?:best|top)\s+(?:\d{1,3}\s+)?(?:computer science|tech|engineering|coding|stem|cloud|internships|scholarships|fellowships|hackathons|colleges|universities|programs|schools|undergraduate)\b",
    # "Scholarship Search", "Job Portal"
    r"\b(?:scholarship|internship|job|fellowship|grant|college)\s+(?:search|aggregator|portal|database)\b",
    # "2027 List of Scholarships...", "Search results for..."
    r"\b(?:list\s+of|search\s+results\s+for|browse\s+all)\s+(?:scholarships|internships|fellowships|hackathons|opportunities)\b",
    # "How to find hackathons..."
    r"\bhow\s+to\s+find\s+(?:hackathons|scholarships|internships|opportunities)\b",
    # "Conferences in USA 2026/2027"
    r"\bconferences?\s+in\s+.*\s+202\d\b",
    # Generic plural roundup title without a programme name:
    # "Cloud Computing and Tech Internships 2027", "International IT and Computer Science Internships", "Computer Science Internships in Washington, DC"
    r"^(?:(?:[A-Za-z\s]+(?:and|&)\s+)?[A-Za-z\s]+)?(?:internships|scholarships|fellowships|grants|hackathons|opportunities)(?:\s+in\s+[A-Za-z\s,]+)?(?:\s+202\d)?(?:\s*\|.*)?$",
    # "Undergraduate Computer Science Programs Rankings", "Best Colleges for Computer Science"
    r"\b(?:rankings?|best\s+colleges|best\s+undergraduate\s+programs)\b",
]

_ROUNDUP_URL_PATTERNS = [
    r"/search/scholarships/",
    r"/search/internships/",
    r"/discipline\.aspx\?sort=",
    r"/scholarships-overview/",
    r"/scholarships/finder",
    r"/scholarship-finder",
    r"/degree.*/list-of-scholarships",
    r"/conferences/.*conferenceindex",
]


def is_roundup_or_directory(
    title: str,
    organizer: str = "",
    source_url: str = "",
    application_url: str = "",
) -> tuple[bool, str]:
    """Check if an opportunity is a listicle, directory, finder, or multi-item aggregator.

    Returns (is_roundup, reason).
    """
    clean_title = (title or "").strip()
    clean_org = (organizer or "").strip()
    clean_source = (source_url or "").strip().lower()
    clean_apply = (application_url or "").strip().lower()

    # 1. Check title patterns
    for pat in _ROUNDUP_TITLE_PATTERNS:
        if re.search(pat, clean_title, re.IGNORECASE):
            return True, f"Title matches roundup/directory pattern: {pat}"

    # 2. Check URL patterns
    for url in (clean_source, clean_apply):
        if url:
            for upat in _ROUNDUP_URL_PATTERNS:
                if re.search(upat, url, re.IGNORECASE):
                    return True, f"URL matches directory/finder pattern: {upat}"

    # 3. Require each item to have one organiser and one programme:
    # Multiple distinct companies/organisers listed (e.g. 3+ comma-separated distinct entities)
    if clean_org:
        parts = [p.strip() for p in re.split(r"[,;/]\s*", clean_org) if len(p.strip()) > 1]
        if len(parts) >= 3 and not (
            "university" in clean_org.lower() and "college" in clean_org.lower() and len(parts) < 3
        ):
            return True, f"Multiple distinct organizers listed ({len(parts)} entities): {clean_org}"

    return False, ""
