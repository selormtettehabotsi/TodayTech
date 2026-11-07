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
    """Verify that domain verification downgrades aggregator/blog links."""
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
    assert len(results) == 1
    item = results[0]
    assert item.date_status in (DateStatus.ESTIMATED, DateStatus.UNKNOWN)
    assert item.conflict is True
    assert "aggregator" in item.date_evidence.lower()


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
