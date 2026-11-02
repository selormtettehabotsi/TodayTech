"""Tests for robust date parsing and GMT 0:00 (UTC) timezone normalization."""

from datetime import date

from todaytech.core.pipeline import _parse_date


def test_parse_iso_with_offsets():
    # UTC/GMT offsets
    assert _parse_date("2026-11-15T07:30:00+00:00") == date(2026, 11, 15)
    assert _parse_date("2026-11-15T07:30:00Z") == date(2026, 11, 15)

    # Convert non-UTC timezone to GMT 0:00 (23:30 in +05:30 on Nov 20 is 18:00 UTC on Nov 20)
    assert _parse_date("2026-11-20T23:30:00+05:30") == date(2026, 11, 20)

    # 02:00 in +05:30 on Nov 21 is 20:30 UTC on Nov 20 (rolls back to prior day in GMT!)
    assert _parse_date("2026-11-21T02:00:00+05:30") == date(2026, 11, 20)


def test_parse_devpost_ranges():
    # End date in same month
    assert _parse_date("Nov 09 - 30, 2026") == date(2026, 11, 30)

    # End date in next month / year
    assert _parse_date("Dec 19, 2026 - Jan 18, 2027") == date(2027, 1, 18)

    # End date with day range
    assert _parse_date("Jan 08 - 10, 2027") == date(2027, 1, 10)


def test_parse_ordinals_and_formats():
    assert _parse_date("Oct 18th - 20th, 2026") == date(2026, 10, 20)
    assert _parse_date("November 30, 2026") == date(2026, 11, 30)
    assert _parse_date("30 November 2026") == date(2026, 11, 30)
    assert _parse_date("15/12/2026") == date(2026, 12, 15)
    assert _parse_date("2026/11/30") == date(2026, 11, 30)
    assert _parse_date("10 Jan 2027") == date(2027, 1, 10)


def test_parse_open_or_none():
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("Rolling") is None
    assert _parse_date("Open enrollment") is None
    assert _parse_date("TBD") is None
    assert _parse_date("null") is None
