"""Tests for partner-delivered programmes validation (Task 5)."""

from todaytech.core.partner_programs import validate_partner_program


def test_generic_mastercard_scholars_excluded():
    """Generic Mastercard Foundation Scholars entry without partner institution is excluded."""
    is_valid, reason = validate_partner_program(
        title="Mastercard Foundation Scholars Program",
        organizer="Mastercard Foundation",
        source_url="https://mastercardfdn.org",
        application_url="https://mastercardfdn.org/apply",
    )
    assert is_valid is False
    assert "partner institution" in reason.lower()


def test_graduate_only_partner_programme_excluded():
    """Sciences Po or Cambridge Mastercard programme for graduate study excludes Ghanaian undergraduate."""
    is_valid, reason = validate_partner_program(
        title="Sciences Po Mastercard Foundation Scholars Program 2027 for Graduate Study in France",
        organizer="Sciences Po in partnership with Mastercard Foundation",
        source_url="https://sciencespo.fr/students/en/fees-funding/bursaries/mastercard",
        application_url="https://sciencespo.fr/apply",
        level_required="Masters / Graduate",
    )
    assert is_valid is False
    assert "graduate" in reason.lower() or "undergraduate" in reason.lower()


def test_valid_partner_institution_undergraduate_accepted():
    """KNUST Mastercard Foundation Scholars Program for Ghanaian undergraduates is accepted."""
    is_valid, reason = validate_partner_program(
        title="Mastercard Foundation Scholars Program at KNUST 2026/2027",
        organizer="KNUST / Mastercard Foundation",
        source_url="https://knust.edu.gh/scholarships/mastercard-foundation-scholars",
        application_url="https://knust.edu.gh/apply",
        level_required="Undergraduate",
    )
    assert is_valid is True
    assert reason == ""


def test_partner_programme_on_aggregator_excluded():
    """Partner programme hosted on an aggregator rather than official university domain is excluded."""
    is_valid, reason = validate_partner_program(
        title="Mastercard Foundation Scholars Program at Ashesi University",
        organizer="Ashesi University",
        source_url="https://opportunitydesk.org/mastercard-ashesi",
        application_url="https://opportunitydesk.org/apply",
    )
    assert is_valid is False
