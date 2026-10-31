"""Tests for campus/student ambassador opportunity support."""

from todaytech.core.models import OpportunityType, UserProfile
from todaytech.core.pipeline import _dict_to_opportunity, generate_queries


def _profile(types: list[str]) -> UserProfile:
    return UserProfile(
        country="Ghana",
        level="undergraduate",
        field_of_study="Computer Science",
        opportunity_types=types,
    )


def _profile_europe(types: list[str]) -> UserProfile:
    return UserProfile(
        country="Ghana",
        level="undergraduate",
        field_of_study="Computer Science",
        opportunity_types=types,
        regions=["Global", "Africa", "Europe", "France"],
    )


class TestAmbassadorQueries:
    def test_ambassador_type_adds_campus_queries(self):
        queries = generate_queries(_profile(["ambassador"]))
        assert any("campus ambassador" in q for q in queries)
        assert any("Ghana" in q and "ambassador" in q for q in queries)

    def test_ambassador_includes_microsoft(self):
        queries = generate_queries(_profile(["ambassador"]))
        assert any("Microsoft" in q for q in queries)

    def test_ambassador_includes_github(self):
        queries = generate_queries(_profile(["ambassador"]))
        assert any("GitHub" in q for q in queries)

    def test_ambassador_includes_aws(self):
        queries = generate_queries(_profile(["ambassador"]))
        assert any("AWS" in q for q in queries)

    def test_no_ambassador_queries_unless_selected(self):
        queries = generate_queries(_profile(["hackathon"]))
        assert not any("ambassador" in q for q in queries)

    def test_ambassador_in_default_profile_types(self):
        assert "ambassador" in UserProfile().opportunity_types


class TestAmbassadorClassification:
    def test_llm_type_ambassador(self):
        opp = _dict_to_opportunity({"title": "Student Program", "type": "ambassador"})
        assert opp.type == OpportunityType.AMBASSADOR

    def test_title_fallback_when_type_unknown(self):
        opp = _dict_to_opportunity({"title": "Notion Campus Ambassador 2026", "type": "other"})
        assert opp.type == OpportunityType.AMBASSADOR

    def test_explicit_type_not_overridden(self):
        opp = _dict_to_opportunity(
            {"title": "Ambassador Scholarship for Women in Tech", "type": "scholarship"}
        )
        assert opp.type == OpportunityType.SCHOLARSHIP


class TestRegionQueries:
    def test_europe_region_adds_eiffel_query(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("Eiffel" in q for q in queries)

    def test_europe_region_adds_erasmus_query(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("Erasmus Mundus" in q for q in queries)

    def test_europe_region_adds_campus_france(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("Campus France" in q for q in queries)

    def test_europe_region_adds_mastercard(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("Mastercard Foundation" in q for q in queries)

    def test_europe_region_adds_daad(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("DAAD" in q for q in queries)

    def test_no_europe_queries_without_region(self):
        profile = UserProfile(
            country="Ghana",
            level="undergraduate",
            field_of_study="Computer Science",
            opportunity_types=["scholarship"],
            regions=["Africa"],
        )
        queries = generate_queries(profile)
        assert not any("Eiffel" in q for q in queries)
        assert not any("Erasmus" in q for q in queries)


class TestLinkedInQueries:
    def test_linkedin_queries_include_site_prefix(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        linkedin_queries = [q for q in queries if "site:linkedin.com" in q]
        assert len(linkedin_queries) >= 4

    def test_linkedin_includes_fellowship(self):
        queries = generate_queries(_profile_europe(["fellowship"]))
        assert any("site:linkedin.com" in q and "fellowship" in q for q in queries)

    def test_linkedin_includes_europe_france(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("site:linkedin.com" in q and ("Europe" in q or "France" in q) for q in queries)

    def test_linkedin_includes_mastercard(self):
        queries = generate_queries(_profile_europe(["scholarship"]))
        assert any("site:linkedin.com" in q and "Mastercard" in q for q in queries)
