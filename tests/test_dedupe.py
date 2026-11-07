"""Tests for the deduplication logic, canonical URL normalization, and roundup filtering."""

from datetime import date

import pytest

from todaytech.core.database import get_session, init_db, upsert_opportunity
from todaytech.core.dedupe import (
    canonicalize_url,
    compute_dedupe_key,
    opportunity_rank,
)
from todaytech.core.roundup_filter import is_roundup_or_directory
from todaytech.scripts.merge_duplicates import run_merge_duplicates


class TestCanonicalizeUrl:
    def test_strips_trailing_slash(self):
        assert canonicalize_url("https://example.com/path/") == "https://example.com/path"

    def test_lowercases_and_drops_www(self):
        assert canonicalize_url("HTTP://WWW.EXAMPLE.COM/Path") == "https://example.com/path"

    def test_forces_https(self):
        assert canonicalize_url("http://example.com/page") == "https://example.com/page"

    def test_removes_fragment(self):
        assert (
            canonicalize_url("https://example.com/page#section-anchor")
            == "https://example.com/page"
        )

    def test_removes_utm_and_tracking_params(self):
        assert (
            canonicalize_url(
                "https://example.com/page?utm_source=twitter&utm_medium=cpc&ref=xyz&id=100"
            )
            == "https://example.com/page?id=100"
        )

    def test_empty_string_and_invalid(self):
        assert canonicalize_url("") == ""
        assert canonicalize_url(None) == ""
        assert canonicalize_url("none") == ""
        assert canonicalize_url("#") == ""


class TestDedupeKeyMatching:
    def test_amalitech_duplicate_variations_produce_same_key(self):
        """Requirement Task 4: Same normalised organisation + programme name + cycle_year."""
        key1 = compute_dedupe_key(
            title="AmaliTech Immersive Internship Programme 2026 (Paid) – Ghana",
            organizer="AmaliTech",
            cycle_year=2026,
            source_url="https://linkedin.com/posts/activity-12345",
        )
        key2 = compute_dedupe_key(
            title="Immersive Internship Programme",
            organizer="AmaliTech",
            cycle_year=2026,
            source_url="https://amalitech.com/immersive-internship-programme/",
        )
        assert key1 == key2
        assert "amalitech:immersive internship:2026" in key1

    def test_different_cycle_years_produce_different_keys(self):
        key2026 = compute_dedupe_key("Google Summer of Code", "Google", 2026)
        key2027 = compute_dedupe_key("Google Summer of Code", "Google", 2027)
        assert key2026 != key2027

    def test_same_official_url_matches(self):
        u1 = canonicalize_url("https://summerofcode.withgoogle.com/")
        u2 = canonicalize_url("http://www.summerofcode.withgoogle.com?utm_source=twitter")
        assert u1 == u2

    def test_blank_or_generic_organizer_kept_separate(self):
        """Task 3: Never merge items whose organiser is blank or generic.

        If missing, derive it from the official domain; if impossible, do not merge.
        Assert that 'Campus Ambassador Program' at cap.giftoflife.org and
        'Campus Ambassador Program 2026' at launchedglobal.in are kept separate.
        """
        key_official = compute_dedupe_key(
            title="Campus Ambassador Program",
            organizer="",  # Blank organizer
            cycle_year=2026,
            source_url="https://cap.giftoflife.org",
        )
        key_aggregator = compute_dedupe_key(
            title="Campus Ambassador Program 2026",
            organizer="",  # Blank organizer
            cycle_year=2026,
            source_url="https://launchedglobal.in/campus-ambassador",
        )
        assert key_official != key_aggregator
        assert "giftoflife" in key_official
        assert "launchedglobal" not in key_official


class TestPrecedenceMerging:
    def test_prefers_confirmed_over_estimated_and_unknown(self):
        """Requirement Task 4: confirmed > estimated > unknown."""

        class Opp:
            def __init__(self, status, src, ver=None, score=80):
                self.date_status = status
                self.source_url = src
                self.verified_on = ver
                self.fit_score = score
                self.organizer = "Org"

        opp_conf = Opp("confirmed", "https://example.com/official", date(2026, 11, 1))
        opp_est = Opp("estimated", "https://example.com/official", date(2026, 11, 1))
        opp_unk = Opp("unknown", "https://example.com/official", date(2026, 11, 1))

        assert opportunity_rank(opp_conf) > opportunity_rank(opp_est)
        assert opportunity_rank(opp_est) > opportunity_rank(opp_unk)

    def test_prefers_official_domain_over_aggregator(self):
        class Opp:
            def __init__(self, src):
                self.date_status = "confirmed"
                self.source_url = src
                self.verified_on = date(2026, 11, 1)
                self.fit_score = 80
                self.organizer = "AmaliTech"

        opp_official = Opp("https://amalitech.com/internship")
        opp_aggregator = Opp("https://opportunitydesk.org/internship")
        assert opportunity_rank(opp_official) > opportunity_rank(opp_aggregator)

    def test_prefers_more_recently_verified(self):
        class Opp:
            def __init__(self, ver):
                self.date_status = "confirmed"
                self.source_url = "https://example.com/official"
                self.verified_on = ver
                self.fit_score = 80
                self.organizer = "Org"

        opp_newer = Opp(date(2026, 11, 7))
        opp_older = Opp(date(2026, 10, 1))
        assert opportunity_rank(opp_newer) > opportunity_rank(opp_older)

    def test_merge_two_opportunity_records_official_url_rules(self):
        """Task 3: Never let a non-official URL replace an official one when merging;

        if no merged record has an official URL, drop the item.
        """
        from todaytech.core.dedupe import merge_two_opportunity_records

        official_rec = {
            "title": "Internship",
            "organizer": "AmaliTech",
            "source_url": "https://amalitech.com/internship",
            "application_url": "https://amalitech.com/apply",
            "reward": "Stipend",
        }
        aggregator_rec = {
            "title": "Internship",
            "organizer": "AmaliTech",
            "source_url": "https://linkedin.com/posts/activity-123",
            "application_url": "",
            "location": "Accra, Ghana",
        }

        # Winner is official, loser is aggregator -> official URL preserved
        merged1 = merge_two_opportunity_records(official_rec, aggregator_rec)
        assert merged1 is not None
        assert merged1["source_url"] == "https://amalitech.com/internship"
        assert merged1["location"] == "Accra, Ghana"

        # Winner is aggregator, loser is official -> loser's official URL replaces aggregator
        merged2 = merge_two_opportunity_records(aggregator_rec, official_rec)
        assert merged2 is not None
        assert merged2["source_url"] == "https://amalitech.com/internship"

        # Neither has official URL -> item dropped (returns None)
        aggregator_rec2 = {
            "title": "Internship",
            "organizer": "Unknown",
            "source_url": "https://devpost.com/hackathons/1",
            "application_url": "https://devpost.com/apply",
        }
        dropped = merge_two_opportunity_records(aggregator_rec, aggregator_rec2)
        assert dropped is None


class TestRoundupRejection:
    """Requirement Task 3: Reject listicles, directories, finders, and multi-organiser roundups."""

    def test_rejects_listicles_and_finders(self):
        roundup_titles = [
            "Cloud Computing and Tech Internships 2027",
            "International IT and Computer Science Internships",
            "Cloud Computing Internships in Ghana: Find 40 Best...",
            "27 Scholarships for Computer Sciences in Ghana",
            "US News Scholarship Finder – Scholarships for Computer and Information Sciences",
            "Computer Sciences: Directory of Internships, Research Opportunities, Scholarships & Fellowships",
            "2027 List of Scholarships for Computer Science Majors | Research.com",
            "How To Find Hackathons",
            "The Best Hackathons for High School Students",
            "Top 75 Computer Science Scholarships (September 2026) | Bold.org",
        ]
        for title in roundup_titles:
            is_roundup, reason = is_roundup_or_directory(title)
            assert is_roundup is True, f"Failed to reject roundup: '{title}'"
            assert len(reason) > 0

    def test_rejects_extended_roundups_and_curated_repos(self):
        """Task 2: Reject titles with number + fully funded/best/top/scholarships,

        curated lists, finders, directories, degree programs, Fully Funded for nationality students,
        and GitHub repository names.
        """
        exact_required_titles = [
            "104 Fully Funded Computer Science scholarships for ghanaian students in canada",
            "Fully Funded Scholarships for Ghana Students 2027-2028",
            "Julian048/CS-Everything-but-Internships (Curated List of CS Programs & Fellowships)",
            "2027 Online Computer Science Bachelor's Degree Programs with Monthly Start Dates",
        ]
        for title in exact_required_titles:
            is_roundup, reason = is_roundup_or_directory(title)
            assert is_roundup is True, f"Failed to reject exact required title: '{title}'"
            assert len(reason) > 0

    def test_allows_single_programme_opportunities(self):
        single_opportunities = [
            ("Google Summer of Code 2027 Mentee Program", "Google Open Source"),
            ("ETHGlobal AI & Web3 Autonomous Hackathon", "ETHGlobal"),
            ("Mozilla Technology Fund: Open Source AI Grant", "Mozilla Foundation"),
            ("Mastercard Foundation Scholars Program", "Mastercard Foundation"),
            ("AmaliTech Immersive Internship Programme", "AmaliTech"),
            ("Microsoft Learn Student Ambassadors", "Microsoft"),
        ]
        for title, org in single_opportunities:
            is_roundup, _ = is_roundup_or_directory(title, org)
            assert is_roundup is False, f"Erroneously rejected legitimate opportunity: '{title}'"

    def test_rejects_multi_company_organizers(self):
        is_roundup, _ = is_roundup_or_directory(
            "Summer Cloud Practicum",
            "Microsoft, Google, Amazon, IBM, Cisco, SAP",
        )
        assert is_roundup is True


@pytest.mark.asyncio
async def test_upsert_dedupes_against_database_and_merges(tmp_path):
    """Requirement Task 4: Dedupe on every scan against the database, not just within the batch."""
    db_file = tmp_path / "dedupe_test.db"
    await init_db(f"sqlite+aiosqlite:///{db_file.as_posix()}")

    item1 = {
        "external_id": "ext_amalitech_linkedin",
        "title": "AmaliTech Immersive Internship Programme 2026 (Paid) – Ghana",
        "type": "internship",
        "organizer": "AmaliTech",
        "date_status": "unknown",
        "deadline": None,
        "cycle_year": 2026,
        "source_url": "https://www.linkedin.com/posts/activity-987",
        "application_url": "",
        "reward": "Paid Monthly Stipend",
        "location": "Takoradi / Accra, Ghana",
        "field_required": "Software Engineering",
    }

    item2 = {
        "external_id": "ext_amalitech_official",
        "title": "Immersive Internship Programme",
        "type": "internship",
        "organizer": "AmaliTech",
        "date_status": "confirmed",
        "deadline": date(2026, 12, 15),
        "cycle_year": 2026,
        "source_url": "https://amalitech.com/immersive-internship-programme/",
        "application_url": "https://amalitech.com/apply",
        "reward": "",
        "location": "Ghana",
        "field_required": "Software Engineering",
    }

    async with get_session() as session:
        # Insert item 1 (unknown status from LinkedIn)
        row1, is_new1 = await upsert_opportunity(session, item1)
        await session.commit()
        assert is_new1 is True
        assert row1.date_status == "unknown"

        # Now scan encounters item 2 (confirmed status from official domain)
        row2, is_new2 = await upsert_opportunity(session, item2)
        await session.commit()
        assert is_new2 is False  # Merged into existing row!
        assert row2.id == row1.id
        assert row2.date_status == "confirmed"
        assert row2.deadline == date(2026, 12, 15)
        # Retained non-empty reward from item 1
        assert "Paid Monthly Stipend" in row2.reward
        assert "amalitech.com/apply" in row2.application_url


def test_one_off_migration_script(tmp_path):
    """Requirement Task 4: Verify migration script merges existing duplicates and purges roundups."""
    import sqlite3

    db_file = tmp_path / "test_migration.db"
    con = sqlite3.connect(db_file)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id VARCHAR(256) UNIQUE,
            title VARCHAR(512),
            type VARCHAR(64),
            organizer VARCHAR(256),
            deadline DATE,
            cycle_year INTEGER,
            reward TEXT,
            location VARCHAR(256),
            application_url VARCHAR(1024),
            source_url VARCHAR(1024),
            date_status VARCHAR(32) DEFAULT 'unknown',
            estimated_window VARCHAR(256),
            verified_on DATE,
            fit_score INTEGER DEFAULT 80
        )
    """)
    cur.execute("""
        CREATE TABLE user_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id VARCHAR(256),
            status VARCHAR(32) DEFAULT 'saved'
        )
    """)

    # Row 1: Roundup page (to be purged)
    cur.execute("""
        INSERT INTO opportunities (external_id, title, organizer, source_url)
        VALUES ('roundup_1', '27 Scholarships for Computer Sciences in Ghana', 'Aggregator', 'https://bachelorsportal.com/search')
    """)

    # Row 2: AmaliTech from aggregator (to be merged)
    cur.execute("""
        INSERT INTO opportunities (external_id, title, organizer, cycle_year, date_status, source_url, reward)
        VALUES ('amalitech_agg', 'AmaliTech Immersive Internship Programme 2026 (Paid) – Ghana', 'AmaliTech', 2026, 'unknown', 'https://linkedin.com/posts/1', 'Paid Stipend')
    """)

    # Row 3: AmaliTech official (winner)
    cur.execute("""
        INSERT INTO opportunities (external_id, title, organizer, cycle_year, date_status, source_url, application_url)
        VALUES ('amalitech_off', 'Immersive Internship Programme', 'AmaliTech', 2026, 'confirmed', 'https://amalitech.com/immersive-internship-programme', 'https://amalitech.com/apply')
    """)

    # User saved the aggregator entry
    cur.execute("INSERT INTO user_state (opportunity_id, status) VALUES ('amalitech_agg', 'saved')")
    con.commit()
    con.close()

    # Run migration script
    report = run_merge_duplicates(str(db_file))
    assert report["roundups_removed"] == 1
    assert report["duplicate_groups_merged"] == 1
    assert report["duplicates_removed"] == 1
    assert report["final_total"] == 1

    # Verify database state after merge
    con = sqlite3.connect(db_file)
    cur = con.cursor()
    cur.execute(
        "SELECT external_id, title, date_status, reward, application_url FROM opportunities"
    )
    remaining = cur.fetchall()
    assert len(remaining) == 1
    winner = remaining[0]
    assert winner[0] == "amalitech_off"
    assert winner[2] == "confirmed"
    assert winner[3] == "Paid Stipend"

    # Verify user_state re-pointed to winning external_id
    cur.execute("SELECT opportunity_id FROM user_state")
    assert cur.fetchone()[0] == "amalitech_off"
    con.close()
