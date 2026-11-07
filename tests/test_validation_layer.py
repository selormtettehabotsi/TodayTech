"""Test Task 2 & Task 3: Scanner prompt, defensive parsing, and validation layer."""

from datetime import date

from todaytech.core.llm import parse_defensive_json_array
from todaytech.core.models import DateStatus
from todaytech.core.pipeline import SCANNER_PROMPT_TEMPLATE, validate_opportunity_batch


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


def test_validation_layer_drop_missing_source_url():
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
    results = validate_opportunity_batch(items, today)
    assert len(results) == 1
    assert results[0].title == "Valid Source URL Item"


def test_validation_layer_drop_past_deadline():
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
    results = validate_opportunity_batch(items, today)
    assert len(results) == 1
    assert results[0].title == "Future Hackathon"
    assert results[0].days_remaining == 8


def test_validation_layer_reject_year_mismatch_downgrade_to_estimated():
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
    results = validate_opportunity_batch(items, today)
    assert len(results) == 1
    gsoc = results[0]
    assert gsoc.date_status == DateStatus.ESTIMATED
    assert gsoc.deadline is None
    assert gsoc.days_remaining is None
    assert "2027" in (gsoc.estimated_window or "")


def test_validation_layer_deduplicate_by_apply_url():
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
    results = validate_opportunity_batch(items, today)
    assert len(results) == 1


def test_validation_layer_cap_at_25():
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
    results = validate_opportunity_batch(items, today, max_cap=25)
    assert len(results) == 25
