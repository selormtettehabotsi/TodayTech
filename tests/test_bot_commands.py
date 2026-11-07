"""Unit tests for TodayTech Telegram bot date accuracy commands."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from todaytech.bot.app import (
    _format_details,
    cmd_deadlines,
    cmd_estimated,
    cmd_ip,
    cmd_save,
    cmd_saved,
)
from todaytech.core.database import get_session, init_db, upsert_opportunity
from todaytech.core.models import DateStatus


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path):
    await init_db(f"sqlite+aiosqlite:///{(tmp_path / 'test_bot.db').as_posix()}")


@pytest.mark.asyncio
async def test_cmd_deadlines_confirmed_only():
    """Verify /deadlines only returns confirmed opportunities and sorts by nearest deadline."""
    today = date.today()
    async with get_session() as session:
        # 1. Confirmed closing in 5 days
        await upsert_opportunity(
            session,
            {
                "external_id": "conf_01",
                "title": "Confirmed Hackathon Near",
                "type": "hackathon",
                "deadline": today + timedelta(days=5),
                "date_status": DateStatus.CONFIRMED.value,
                "source_url": "https://official.org/dates",
                "verified_on": today,
            },
        )
        # 2. Confirmed closing in 20 days
        await upsert_opportunity(
            session,
            {
                "external_id": "conf_02",
                "title": "Confirmed Scholarship Far",
                "type": "scholarship",
                "deadline": today + timedelta(days=20),
                "date_status": DateStatus.CONFIRMED.value,
                "source_url": "https://official2.org/dates",
                "verified_on": today,
            },
        )
        # 3. Estimated opportunity (should NOT be returned in /deadlines)
        await upsert_opportunity(
            session,
            {
                "external_id": "est_01",
                "title": "Google Summer of Code 2027",
                "type": "fellowship",
                "deadline": None,
                "date_status": DateStatus.ESTIMATED.value,
                "estimated_window": "Late March 2027",
                "source_url": "https://summerofcode.withgoogle.com",
            },
        )
        # 4. Unknown date status opportunity (should NOT be returned in /deadlines)
        await upsert_opportunity(
            session,
            {
                "external_id": "unk_01",
                "title": "Unverified Grant",
                "type": "grant",
                "deadline": today + timedelta(days=10),
                "date_status": DateStatus.UNKNOWN.value,
                "source_url": "https://unverified.org",
            },
        )
        await session.commit()

    # Mock Telegram Update & Context
    update = MagicMock()
    update.effective_user.id = 1291866098
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    await cmd_deadlines(update, context)

    # Verify response
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "Confirmed Hackathon Near" in reply_text
    assert "Confirmed Scholarship Far" in reply_text
    # Nearest deadline must appear before farther deadline
    assert reply_text.index("Confirmed Hackathon Near") < reply_text.index(
        "Confirmed Scholarship Far"
    )
    # Must NOT include estimated or unknown items
    assert "Google Summer of Code 2027" not in reply_text
    assert "Unverified Grant" not in reply_text


@pytest.mark.asyncio
async def test_cmd_estimated():
    """Verify /estimated returns estimated items with estimated window and source URL."""
    today = date.today()
    async with get_session() as session:
        await upsert_opportunity(
            session,
            {
                "external_id": "est_gsoc",
                "title": "Google Summer of Code 2027",
                "type": "fellowship",
                "deadline": None,
                "date_status": DateStatus.ESTIMATED.value,
                "estimated_window": "likely late March 2027",
                "source_url": "https://summerofcode.withgoogle.com",
                "fit_score": 95,
                "verified_on": today,
            },
        )
        await session.commit()

    update = MagicMock()
    update.effective_user.id = 1291866098
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    await cmd_estimated(update, context)

    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "Google Summer of Code 2027" in reply_text
    assert "[ESTIMATED]" in reply_text
    assert "likely late March 2027" in reply_text
    assert "https://summerofcode.withgoogle.com" in reply_text


def test_format_details():
    """Verify _format_details outputs date_status, verified_on, source_url, date_evidence."""
    opp = MagicMock(
        title="MLH Global Hackathon",
        external_id="mlh12345678",
        type="hackathon",
        organizer="Major League Hacking",
        date_status="confirmed",
        deadline=date(2026, 12, 1),
        estimated_window=None,
        verified_on=date(2026, 11, 7),
        date_evidence="Dates confirmed on official mlh.io 2026 schedule",
        start_date=date(2026, 12, 5),
        location="Remote",
        mode="online",
        cost=0.0,
        reward="$10,000",
        eligibility_verdict="Eligible",
        eligibility_reason="Open to all university students",
        eligibility_text="Students worldwide aged 18+",
        fit_score=92,
        fit_reason="Direct match for CS skills",
        is_scam=False,
        scam_reason="",
        source_url="https://mlh.io/season/2026/events",
        application_url="https://mlh.io/apply",
    )

    formatted = _format_details(opp)
    assert "Date Status: `CONFIRMED`" in formatted
    assert "Confirmed Deadline: `2026-12-01`" in formatted
    assert "Verified On: `2026-11-07`" in formatted
    assert "Date Evidence: Dates confirmed on official mlh.io 2026 schedule" in formatted
    assert "Official Source: https://mlh.io/season/2026/events" in formatted
    assert "Application URL: https://mlh.io/apply" in formatted


@pytest.mark.asyncio
async def test_cmd_ip_returns_public_ip_and_dashboard_link():
    """Verify /ip command returns server public IP and public dashboard URL."""
    update = MagicMock()
    update.effective_user.id = 1291866098
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    await cmd_ip(update, context)

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Server Public IP" in reply
    assert "http://" in reply


@pytest.mark.asyncio
async def test_bot_auth_rejects_unauthorized_users():
    """Verify bot ignores or rejects requests from anyone other than the owner."""
    update = MagicMock()
    update.effective_user.id = 999999999  # Unauthorized ID
    update.effective_user.username = "intruder"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    # Attempt to run /deadlines
    await cmd_deadlines(update, context)

    # Should reply stating unauthorized and showing their numeric ID
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999999999" in reply
    assert "configured for User ID" in reply
    assert "Upcoming Confirmed Deadlines" not in reply


@pytest.mark.asyncio
async def test_bot_owner_can_save_and_view_pipeline():
    """Verify bot owner can save opportunities and list saved pipeline."""
    today = date.today()
    async with get_session() as session:
        await upsert_opportunity(
            session,
            {
                "external_id": "opp_save_test",
                "title": "Pipeline Save Target",
                "type": "fellowship",
                "deadline": today + timedelta(days=14),
                "date_status": DateStatus.CONFIRMED.value,
                "source_url": "https://target.org",
            },
        )
        await session.commit()

    update = MagicMock()
    update.effective_user.id = 1291866098
    update.message = MagicMock()
    update.message.text = "save opp_save"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    # Save
    await cmd_save(update, context)
    update.message.reply_text.assert_called_once()
    assert "Saved" in update.message.reply_text.call_args[0][0]

    # View saved list
    update.message.reply_text.reset_mock()
    await cmd_saved(update, context)
    update.message.reply_text.assert_called_once()
    assert "opp_save_test" in update.message.reply_text.call_args[0][0]
