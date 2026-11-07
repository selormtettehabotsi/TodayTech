"""Unit tests for TodayTech Telegram bot date accuracy commands."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from todaytech.bot.app import (
    _format_details,
    cmd_apply,
    cmd_deadlines,
    cmd_details,
    cmd_estimated,
    cmd_ignore,
    cmd_ip,
    cmd_save,
    cmd_saved,
    cmd_start,
    cmd_update,
    handle_message,
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
    """Task 4: Verify /estimated returns estimated items with previous cycle evidence,

    and excludes items with only 'Expected window TBA' or non-official sources.
    """
    today = date.today()
    async with get_session() as session:
        # Valid estimated item with previous cycle evidence
        await upsert_opportunity(
            session,
            {
                "external_id": "est_gsoc",
                "title": "Google Summer of Code 2027",
                "organizer": "Google",
                "type": "fellowship",
                "deadline": None,
                "date_status": DateStatus.ESTIMATED.value,
                "estimated_window": "likely late March 2027, based on the 2026 cycle",
                "prev_cycle_date": date(2026, 4, 8),
                "prev_cycle_source": "https://summerofcode.withgoogle.com/archive",
                "source_url": "https://summerofcode.withgoogle.com",
                "fit_score": 95,
                "verified_on": today,
            },
        )
        # Invalid estimated item: only "Expected window TBA" with no evidence
        await upsert_opportunity(
            session,
            {
                "external_id": "est_tba_no_evidence",
                "title": "Unannounced Tech Fellowship 2027",
                "organizer": "Tech Org",
                "type": "fellowship",
                "deadline": None,
                "date_status": DateStatus.ESTIMATED.value,
                "estimated_window": "Expected window TBA",
                "prev_cycle_date": None,
                "prev_cycle_source": "",
                "date_evidence": "",
                "source_url": "https://techorg.org/fellowship",
                "fit_score": 90,
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
    assert "2026-04-08" in reply_text
    # Item with only "Expected window TBA" and no evidence must NOT be shown!
    assert "Unannounced Tech Fellowship 2027" not in reply_text


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

    # Should reply stating unauthorized without revealing owner ID or configuration
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Access restricted" in reply
    assert "private" in reply
    assert "1291866098" not in reply
    assert "configured" not in reply.lower()
    assert "owner" not in reply.lower()
    assert "Upcoming Confirmed Deadlines" not in reply


@pytest.mark.parametrize(
    ("command_func", "unauthorized_id", "extra_args"),
    [
        (cmd_update, 200000001, {}),
        (cmd_apply, 200000002, {}),
        (cmd_saved, 200000003, {}),
        (cmd_ip, 200000004, {}),
        (cmd_deadlines, 200000005, {}),
        (cmd_estimated, 200000006, {}),
        (cmd_details, 200000007, {}),
        (cmd_save, 200000008, {}),
        (cmd_ignore, 200000009, {}),
        (cmd_start, 200000010, {}),
        (handle_message, 200000011, {"text": "update"}),
    ],
)
@pytest.mark.asyncio
async def test_every_command_rejects_unauthorized_user_with_distinct_id(
    command_func, unauthorized_id, extra_args
):
    """Requirement Task 2: Verify that every bot handler rejects non-owner users

    with a different user ID per command, preventing scans, /apply, /saved, or /ip,
    and never reveals the owner's Telegram user ID or internal configuration.
    """
    update = MagicMock()
    update.effective_user.id = unauthorized_id
    update.effective_user.username = f"intruder_{unauthorized_id}"
    update.message = MagicMock()
    update.message.text = extra_args.get("text", "/test")
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"settings": MagicMock(telegram_user_id=1291866098)}

    await command_func(update, context)

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Access restricted" in reply
    assert "private" in reply
    # Must NEVER reveal owner ID or internal configuration
    assert "1291866098" not in reply
    assert "configured" not in reply.lower()
    assert "owner" not in reply.lower()
    assert "Server Public IP" not in reply
    assert "Active Matches" not in reply
    assert "Tailored Application Assets" not in reply


@pytest.mark.asyncio
async def test_ip_watcher_simulated_change_sends_telegram_alert(tmp_path, monkeypatch):
    """Requirement Task 3: Verify IP watcher detects IP change and sends Telegram alert to owner."""
    import todaytech.scheduler.jobs as jobs_mod

    # Reset initial state
    jobs_mod._last_known_ip = None
    settings = MagicMock(telegram_user_id=1291866098, data_dir=tmp_path)

    context = MagicMock()
    context.job.data = {"settings": settings}
    context.bot.send_message = AsyncMock()

    # Step 1: Initial check with IP 3.91.100.116 (initializes baseline, sends no alert)
    class MockClientInitial:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            res = MagicMock()
            res.status_code = 200
            res.text = "3.91.100.116"
            return res

    monkeypatch.setattr(jobs_mod.httpx, "AsyncClient", lambda **kw: MockClientInitial())

    await jobs_mod._ip_watcher_job(context)
    assert jobs_mod.get_last_known_ip(settings) == "3.91.100.116"
    context.bot.send_message.assert_not_called()

    # Step 2: Second check with unchanged IP -> no alert
    await jobs_mod._ip_watcher_job(context)
    context.bot.send_message.assert_not_called()

    # Step 3: IP changes to 54.210.85.120 -> sends Telegram alert
    class MockClientChanged:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            res = MagicMock()
            res.status_code = 200
            res.text = "54.210.85.120"
            return res

    monkeypatch.setattr(jobs_mod.httpx, "AsyncClient", lambda **kw: MockClientChanged())

    await jobs_mod._ip_watcher_job(context)
    assert jobs_mod.get_last_known_ip(settings) == "54.210.85.120"
    context.bot.send_message.assert_called_once()
    msg = context.bot.send_message.call_args.kwargs.get(
        "text"
    ) or context.bot.send_message.call_args[1].get("text")
    assert "TodayTech IP Update Alert" in msg
    assert "3.91.100.116" in msg
    assert "54.210.85.120" in msg
    assert "http://54.210.85.120/" in msg


@pytest.mark.asyncio
async def test_ip_watcher_startup_detects_changed_ip_and_alerts(tmp_path, monkeypatch):
    """Requirement Task 3: Verify that on bot startup, if the server public IP

    has changed from the previously persisted IP, an immediate Telegram alert is dispatched.
    """
    import todaytech.scheduler.jobs as jobs_mod

    # 1. Simulate prior run that recorded IP 3.91.100.116 to persistent disk storage
    settings = MagicMock(telegram_user_id=1291866098, data_dir=tmp_path)
    jobs_mod._last_known_ip = None
    jobs_mod.set_last_known_ip("3.91.100.116", settings)
    assert (tmp_path / "last_known_ip.txt").read_text().strip() == "3.91.100.116"

    # Reset in-memory cache to simulate full process restart
    jobs_mod._last_known_ip = None

    # 2. Simulate startup with changed public IP 54.210.85.120
    class MockClientStartupChanged:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            res = MagicMock()
            res.status_code = 200
            res.text = "54.210.85.120"
            return res

    monkeypatch.setattr(jobs_mod.httpx, "AsyncClient", lambda **kw: MockClientStartupChanged())

    context = MagicMock()
    context.job.data = {"settings": settings}
    context.bot.send_message = AsyncMock()

    # 3. Startup run of _ip_watcher_job (at first=timedelta(seconds=1))
    await jobs_mod._ip_watcher_job(context)

    # 4. Verify Telegram message was dispatched immediately at startup
    context.bot.send_message.assert_called_once()
    msg = context.bot.send_message.call_args.kwargs.get(
        "text"
    ) or context.bot.send_message.call_args[1].get("text")
    assert "TodayTech IP Update Alert" in msg
    assert "3.91.100.116" in msg
    assert "54.210.85.120" in msg
    assert "http://54.210.85.120/" in msg

    # 5. Verify persisted IP on disk is updated to new IP
    assert (tmp_path / "last_known_ip.txt").read_text().strip() == "54.210.85.120"
    assert jobs_mod.get_last_known_ip(settings) == "54.210.85.120"


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
