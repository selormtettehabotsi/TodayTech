"""Test Task 1, 2, 3 & 4: Scanner prompt, defensive parsing, validation layer, and server-side verification."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from todaytech.core.llm import parse_defensive_json_array
from todaytech.core.models import DateStatus
from todaytech.core.pipeline import (
    SCANNER_PROMPT_TEMPLATE,
    scan_with_gemini_grounded,
    validate_opportunity_batch,
)


def test_defensive_json_parsing_code_fences():
    raw_with_fences = """Here is the result:
```json
[
  {
    "title": "Hackathon 1",
    "source_url": "https://example.com/hack1",
    "date_status": "confirmed",
    "deadline": "2026-11-20"
  },
]
```
Let me know if you need more info.
"""
    parsed = parse_defensive_json_array(raw_with_fences)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Hackathon 1"
    assert parsed[0]["source_url"] == "https://example.com/hack1"


def test_scanner_prompt_date_injection():
    today_str = "2026-11-07"
    tz_str = "GMT 0:00"
    rendered = SCANNER_PROMPT_TEMPLATE.format(
        today=today_str,
        timezone=tz_str,
        name="Selorm",
        level="undergraduate",
        field_of_study="Computer Science",
        university="KNUST",
        country="Ghana",
        citizenship="Ghana",
        fields="Cloud, DevOps, AI/ML",
        types="hackathon, internship, scholarship",
    )
    assert "Today's date is 2026-11-07 (GMT 0:00)" in rendered
    assert "KNUST" in rendered
    assert "Ghana" in rendered


@pytest.mark.asyncio
async def test_validation_layer_drop_missing_source_url():
    today = date(2026, 11, 7)
    items = [
        {
            "title": "No Source URL Item",
            "source_url": "",
            "apply_url": "https://example.com/apply1",
            "date_status": "confirmed",
            "deadline": "2026-11-20",
        },
        {
            "title": "Null Source URL Item",
            "source_url": "null",
            "apply_url": "https://example.com/apply2",
            "date_status": "confirmed",
            "deadline": "2026-11-20",
        },
        {
            "title": "Valid Source URL Item",
            "source_url": "https://example.com/official",
            "apply_url": "https://example.com/apply3",
            "date_status": "confirmed",
            "cycle_year": 2026,
            "deadline": "2026-11-20",
        },
    ]
    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 1
    assert results[0].title == "Valid Source URL Item"


@pytest.mark.asyncio
async def test_validation_layer_drop_past_deadline():
    today = date(2026, 11, 7)
    items = [
        {
            "title": "Past Hackathon",
            "source_url": "https://example.com/past",
            "apply_url": "https://example.com/past/apply",
            "date_status": "confirmed",
            "cycle_year": 2026,
            "deadline": "2026-11-01",  # Past
        },
        {
            "title": "Future Hackathon",
            "source_url": "https://example.com/future",
            "apply_url": "https://example.com/future/apply",
            "date_status": "confirmed",
            "cycle_year": 2026,
            "deadline": "2026-11-15",  # Future
        },
    ]
    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 1
    assert results[0].title == "Future Hackathon"
    assert results[0].days_remaining == 8


@pytest.mark.asyncio
async def test_validation_layer_reject_year_mismatch_downgrade_to_estimated():
    """GSoC 2027 should not present a 2025/2026 deadline as confirmed."""
    today = date(2026, 11, 7)
    items = [
        {
            "title": "Google Summer of Code 2027",
            "source_url": "https://summerofcode.withgoogle.com",
            "apply_url": "https://summerofcode.withgoogle.com/apply",
            "cycle_year": 2027,
            "date_status": "confirmed",
            "deadline": "2025-04-08",  # Mismatch: 2025 vs cycle 2027
        }
    ]
    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 1
    gsoc = results[0]
    assert gsoc.date_status == DateStatus.ESTIMATED
    assert gsoc.deadline is None
    assert gsoc.days_remaining is None
    assert "2027" in (gsoc.estimated_window or "")


@pytest.mark.asyncio
async def test_validation_layer_deduplicate_by_apply_url():
    today = date(2026, 11, 7)
    items = [
        {
            "title": "First Entry",
            "source_url": "https://example.com/page1",
            "apply_url": "https://example.com/apply?utm_source=aggregator",
            "date_status": "rolling",
        },
        {
            "title": "Duplicate Entry with different tracking params",
            "source_url": "https://example.com/page2",
            "apply_url": "https://example.com/apply?utm_source=twitter",
            "date_status": "rolling",
        },
    ]
    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_validation_layer_cap_at_25():
    today = date(2026, 11, 7)
    items = []
    for i in range(35):
        items.append(
            {
                "title": f"Opportunity {i}",
                "source_url": f"https://example.com/opp/{i}",
                "apply_url": f"https://example.com/apply/{i}",
                "date_status": "confirmed",
                "cycle_year": 2026,
                "deadline": "2026-11-20",
                "fit_score": i,
            }
        )
    results = await validate_opportunity_batch(items, today, max_cap=25, verify_pages=False)
    assert len(results) == 25


# ---------------------------------------------------------------------------
# Task 4 Regression Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_gsoc_2027_downgraded_when_page_lacks_date():
    """Task 4 regression test: Model returns GSoC 2027 with matching cycle_year 2027

    and date_status 'confirmed', but the fetched page does NOT contain 2027-04-08.
    Must be downgraded to 'estimated' with conflict=True and deadline set to None.
    """
    today = date(2026, 11, 7)
    items = [
        {
            "title": "Google Summer of Code 2027",
            "source_url": "https://summerofcode.withgoogle.com",
            "apply_url": "https://summerofcode.withgoogle.com/apply",
            "cycle_year": 2027,
            "deadline": "2027-04-08",
            "date_status": "confirmed",
        }
    ]

    # Mock official GSoC webpage containing only older cycle info
    html_response = """
    <html>
        <body>
            <h1>Google Summer of Code</h1>
            <p>Welcome to GSoC. The 2025 program completed on April 8, 2025.</p>
            <p>Dates for the upcoming 2027 cycle will be announced later this year.</p>
        </body>
    </html>
    """

    mock_transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html_response))

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        results = await validate_opportunity_batch(
            items,
            today,
            verify_pages=True,
            client=mock_client,
        )

    assert len(results) == 1
    gsoc = results[0]
    # Stated deadline was 2027-04-08, not present on page -> must downgrade
    assert gsoc.date_status == DateStatus.ESTIMATED
    assert gsoc.deadline is None
    assert gsoc.days_remaining is None
    assert gsoc.conflict is True
    assert gsoc.fetched_on == today
    assert "did not contain stated deadline" in gsoc.date_evidence


@pytest.mark.asyncio
async def test_regression_quota_fallback_forces_unknown():
    """Task 4 regression test: When Google Search grounding hits quota limit or fails,

    scan must never produce confirmed items. All confirmed items must be forced to 'unknown',
    and a Telegram alert must be dispatched.
    """
    profile = MagicMock()
    profile.name = "Selorm"
    profile.level = "undergraduate"
    profile.field_of_study = "Computer Science"
    profile.university = "KNUST"
    profile.country = "Ghana"
    profile.citizenship = ["Ghana"]
    profile.interests = ["AI"]
    profile.opportunity_types = ["hackathon"]

    settings = MagicMock()
    settings.gemini_api_key = "fake_key"
    settings.telegram_bot_token = "fake_token"
    settings.telegram_user_id = 1291866098

    # Simulate quota failure in Gemini LLM
    with (
        patch(
            "todaytech.core.llm.LLMClient.generate_grounded_json_array",
            new=AsyncMock(
                return_value=(
                    [
                        {
                            "name": "Hackathon During Quota",
                            "source_url": "https://hack.org",
                            "date_status": "confirmed",
                            "deadline": "2026-12-01",
                        }
                    ],
                    False,  # is_grounded = False
                    "Google Search grounding hit quota limit (429 / Resource Exhausted)",
                )
            ),
        ),
        patch(
            "todaytech.core.pipeline.send_telegram_alert",
            new=AsyncMock(return_value=True),
        ) as mock_alert,
    ):
        items, is_grounded, reason = await scan_with_gemini_grounded(profile, settings)

        assert is_grounded is False
        assert len(items) == 1
        # Confirmed status must be forced to unknown
        assert items[0]["date_status"] == "unknown"
        assert items[0]["conflict"] is True
        assert "scan ran ungrounded" in items[0]["date_evidence"]

        # Alert must have been sent
        mock_alert.assert_awaited_once()
        sent_alert_text = mock_alert.call_args[0][0]
        assert "Scan Ran Ungrounded" in sent_alert_text
        assert "quota limit" in sent_alert_text.lower()


@pytest.mark.asyncio
async def test_verification_rejects_aggregator_domain():
    """Verify that the official-source gate drops aggregator/blog links outright."""
    today = date(2026, 11, 7)
    items = [
        {
            "title": "Aggregator Sourced Hackathon",
            "source_url": "https://devpost.com/software/my-hack",
            "apply_url": "https://devpost.com/software/my-hack",
            "cycle_year": 2026,
            "deadline": "2026-11-20",
            "date_status": "confirmed",
        }
    ]

    results = await validate_opportunity_batch(items, today, verify_pages=True)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_official_source_gate_drops_all_aggregators_and_social():
    """Task 1: Reject any item, whatever its date_status, whose source_url is on an

    aggregator, social media, job board, blog, curated-list, degree-search or
    scholarship-finder domain.
    """
    today = date(2026, 11, 7)
    disallowed_urls = [
        "https://www.facebook.com/groups/techscholarships/posts/123",
        "https://www.linkedin.com/posts/activity-12345",
        "https://devpost.com/hackathons/ghana-hack-2026",
        "https://dev.events/conferences/africa-tech-2026",
        "https://github.com/Julian048/CS-Everything-but-Internships",
        "https://research.com/scholarships/computer-science",
        "https://www.scholarshipsads.com/fully-funded-ghana-2026",
        "https://scholarshiptab.com/scholarships/undergraduate-cs",
        "https://opportunitiesforafricans.com/tech-fellowship-2026",
        "https://techradar.substack.com/p/best-internships-2026",
        "https://medium.com/@author/top-hackathons-2026",
    ]

    items = [
        {
            "title": f"Opportunity from {url}",
            "source_url": url,
            "apply_url": url,
            "date_status": "confirmed",
            "cycle_year": 2026,
            "deadline": "2026-11-20",
        }
        for url in disallowed_urls
    ]

    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_official_source_gate_promotes_official_apply_url():
    """When source_url is an aggregator lead but application_url is official organizer page,

    the scanner promotes application_url to source_url and preserves the opportunity.
    """
    today = date(2026, 11, 7)
    items = [
        {
            "title": "AmaliTech Immersive Internship Programme",
            "organizer": "AmaliTech",
            "source_url": "https://www.linkedin.com/posts/activity-12345",  # Aggregator/social lead
            "apply_url": "https://amalitech.com/immersive-internship-programme",  # Official page
            "date_status": "confirmed",
            "cycle_year": 2026,
            "deadline": "2026-11-20",
        }
    ]
    results = await validate_opportunity_batch(items, today, verify_pages=False)
    assert len(results) == 1
    assert results[0].source_url == "https://amalitech.com/immersive-internship-programme"


@pytest.mark.asyncio
async def test_verification_confirms_date_when_found_on_official_page():
    """Verify that an official page with the stated deadline preserves confirmed status."""
    today = date(2026, 11, 7)
    items = [
        {
            "title": "Official Hackathon 2026",
            "source_url": "https://officialhackathon.org/schedule",
            "apply_url": "https://officialhackathon.org/apply",
            "cycle_year": 2026,
            "deadline": "2026-11-25",
            "date_status": "confirmed",
        }
    ]

    html_content = """
    <html>
        <body>
            <h1>Official Hackathon 2026</h1>
            <p>Registration deadline is November 25, 2026 at 23:59 GMT.</p>
        </body>
    </html>
    """
    mock_transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html_content))

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        results = await validate_opportunity_batch(
            items,
            today,
            verify_pages=True,
            client=mock_client,
        )

    assert len(results) == 1
    opp = results[0]
    assert opp.date_status == DateStatus.CONFIRMED
    assert opp.conflict is False
    assert opp.deadline == date(2026, 11, 25)
    assert opp.days_remaining == 18
    assert opp.verified_on == today
    assert opp.fetched_on == today
    assert "November 25, 2026" in opp.date_evidence


def test_looks_script_rendered_detection():
    """Task 6: Detect client-side rendered SPA shells."""
    from todaytech.core.verification import looks_script_rendered

    spa_shell = """
    <!DOCTYPE html>
    <html>
    <head><title>Modern SPA App</title></head>
    <body>
        <div id="root"></div>
        <noscript>You need to enable JavaScript to run this app.</noscript>
        <script src="/static/js/bundle.js"></script>
    </body>
    </html>
    """
    assert (
        looks_script_rendered(spa_shell, "You need to enable JavaScript to run this app.") is True
    )

    plain_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <h1>Traditional Server Rendered Page</h1>
        <p>Deadline is December 15, 2026.</p>
    </body>
    </html>
    """
    assert (
        looks_script_rendered(
            plain_html, "Traditional Server Rendered Page Deadline is December 15, 2026."
        )
        is False
    )


@pytest.mark.asyncio
async def test_script_rendered_page_retries_with_headless_browser_and_confirms():
    """Task 6: If plain page fetch finds no dates and looks script-rendered,

    retry with Playwright headless browser before downgrading.
    """
    from todaytech.core.verification import verify_official_page_deadline

    today = date(2026, 11, 7)
    target_deadline = date(2026, 12, 15)

    # 1. Plain HTTP response returns only empty React shell without the deadline
    plain_spa_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <div id="root"></div>
        <noscript>Please enable JavaScript to view applications.</noscript>
        <script src="/bundle.js"></script>
    </body>
    </html>
    """
    mock_transport = httpx.MockTransport(lambda request: httpx.Response(200, text=plain_spa_html))

    # 2. Mock Playwright headless browser rendering the hydrated SPA containing the deadline
    rendered_spa_html = """
    <!DOCTYPE html>
    <html>
    <body>
        <div id="root">
            <main>
                <h1>Client-Side Rendered Hackathon 2026</h1>
                <p>Application deadline: December 15, 2026</p>
            </main>
        </div>
    </body>
    </html>
    """

    with patch(
        "todaytech.core.verification.fetch_page_with_headless_browser",
        new=AsyncMock(return_value=rendered_spa_html),
    ) as mock_playwright:
        async with httpx.AsyncClient(transport=mock_transport) as mock_client:
            verified, status, snippet, explanation = await verify_official_page_deadline(
                source_url="https://officialhackathon.org/spa-app",
                deadline=target_deadline,
                today_date=today,
                client=mock_client,
            )

        # Assert Playwright was called because plain HTML was a script-rendered shell
        mock_playwright.assert_called_once_with("https://officialhackathon.org/spa-app")
        assert verified is True
        assert status == "confirmed"
        assert snippet is not None
        assert "December 15, 2026" in snippet
        assert "headless browser" in explanation
