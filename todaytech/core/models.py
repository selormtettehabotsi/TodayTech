"""Pydantic models for opportunities, user profiles, and extraction schemas."""

from __future__ import annotations

import enum
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OpportunityType(str, enum.Enum):
    HACKATHON = "hackathon"
    INTERNSHIP = "internship"
    SCHOLARSHIP = "scholarship"
    FELLOWSHIP = "fellowship"
    GRANT = "grant"
    PROGRAM = "program"
    COMPETITION = "competition"
    AMBASSADOR = "ambassador"
    OTHER = "other"


class EligibilityVerdict(str, enum.Enum):
    ELIGIBLE = "Eligible"
    LIKELY = "Likely"
    CHECK = "Check"
    NOT_ELIGIBLE = "Not eligible"


class DateStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    ROLLING = "rolling"
    UNKNOWN = "unknown"


class ApplicationStatus(str, enum.Enum):
    SAVED = "saved"
    DRAFTING = "drafting"
    SUBMITTED = "submitted"
    AWAITING = "awaiting"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class LanguageLevel(str, enum.Enum):
    NATIVE = "native"
    FLUENT = "fluent"
    INTERMEDIATE = "intermediate"
    BASIC = "basic"


# ---------------------------------------------------------------------------
# User Profile
# ---------------------------------------------------------------------------


class LanguageEntry(BaseModel):
    language: str
    level: LanguageLevel = LanguageLevel.INTERMEDIATE


class ScoringWeights(BaseModel):
    eligibility: int = 40
    relevance: int = 25
    benefit: int = 20
    effort: int = 15


class UserProfile(BaseModel):
    """User profile loaded from profile.yaml."""

    name: str = ""
    country: str = ""
    citizenship: list[str] = Field(default_factory=list)
    city: str = ""

    university: str = ""
    level: str = "undergraduate"
    year: int = 1
    field_of_study: str = ""

    skills: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    causes: list[str] = Field(default_factory=list)

    languages: list[LanguageEntry] = Field(default_factory=list)

    opportunity_types: list[str] = Field(
        default_factory=lambda: [
            "hackathon",
            "internship",
            "scholarship",
            "fellowship",
            "grant",
            "program",
            "competition",
            "ambassador",
        ]
    )
    regions: list[str] = Field(default_factory=lambda: ["Global"])
    max_application_fee: float = 0

    digest_time: str = "08:00"
    timezone: str = "UTC"

    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    source_packs: list[str] = Field(default_factory=list)

    writing_samples: list[str] = Field(default_factory=list)
    cv_path: str = ""

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, v: object) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            digits = "".join(ch for ch in v if ch.isdigit())
            if digits:
                return int(digits)
        return 1

    @field_validator(
        "citizenship",
        "skills",
        "interests",
        "causes",
        "languages",
        "opportunity_types",
        "regions",
        "source_packs",
        "writing_samples",
        mode="before",
    )
    @classmethod
    def _coerce_none_to_list(cls, v: object) -> object:
        if v is None:
            return []
        return v


# ---------------------------------------------------------------------------
# Opportunity (extraction schema)
# ---------------------------------------------------------------------------


class Opportunity(BaseModel):
    """Normalized opportunity extracted from any source."""

    id: str | None = None
    title: str
    type: OpportunityType = OpportunityType.OTHER
    organizer: str = ""
    deadline: date | None = None
    start_date: date | None = None
    location: str = ""
    mode: str = ""  # online, in-person, hybrid
    cost: float = 0
    eligibility_text: str = ""
    countries_allowed: list[str] = Field(default_factory=list)
    level_required: str = ""
    field_required: str = ""
    reward: str = ""
    application_url: str = ""
    source_url: str = ""
    confidence: float = 0.0  # 0-1 extraction confidence

    # Verification & Date Confidence fields
    date_status: DateStatus = DateStatus.UNKNOWN
    estimated_window: str | None = None
    cycle_year: int | None = None
    date_evidence: str = ""
    conflict: bool = False
    verified_on: date | None = None
    fetched_on: date | None = None
    days_remaining: int | None = None

    # Enriched fields (post-processing)
    fit_score: int = 0  # 0-100
    fit_reason: str = ""
    eligibility_verdict: EligibilityVerdict = EligibilityVerdict.CHECK
    eligibility_reason: str = ""
    is_scam: bool = False
    scam_reason: str = ""

    # Metadata
    source_name: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Digest item (lightweight view)
# ---------------------------------------------------------------------------


class DigestItem(BaseModel):
    """Single item in a user-facing digest."""

    id: str
    title: str
    type: OpportunityType
    organizer: str
    deadline: date | None = None
    fit_score: int = 0
    fit_reason: str = ""
    eligibility_verdict: EligibilityVerdict = EligibilityVerdict.CHECK
    link: str = ""
    date_status: DateStatus = DateStatus.UNKNOWN
    estimated_window: str | None = None
    source_url: str = ""
    verified_on: date | None = None
    fetched_on: date | None = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class ApplicationChecklist(BaseModel):
    """Checklist for an application."""

    opportunity_id: str
    requirements: list[str] = Field(default_factory=list)
    documents_needed: list[str] = Field(default_factory=list)
    user_has: list[str] = Field(default_factory=list)
    user_needs: list[str] = Field(default_factory=list)
    internal_deadline: date | None = None
    status: ApplicationStatus = ApplicationStatus.SAVED


# ---------------------------------------------------------------------------
# Source config
# ---------------------------------------------------------------------------


class SourceConfig(BaseModel):
    """A user-defined source from sources.yaml or a pack."""

    name: str
    type: str  # rss, url, subreddit, search_query, telegram_channel
    value: str  # URL, subreddit name, query string, etc.
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
