"""APScheduler jobs — daily digest and deadline reminders.

The daily digest runs at the user's configured time and timezone.
Deadline reminders fire at 7, 3, and 1 day(s) before each deadline.
"""

from __future__ import annotations

from datetime import date, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from telegram.ext import Application

from todaytech.core.config import Settings
from todaytech.core.models import UserProfile

log = structlog.get_logger()


def setup_scheduler(
    app: Application,
    settings: Settings,
    profile: UserProfile,
) -> None:
    """Register scheduled jobs on the bot's job queue."""
    job_queue = app.job_queue
    if job_queue is None:
        log.warning("scheduler.no_job_queue")
        return

    # Parse digest time
    try:
        hour, minute = map(int, profile.digest_time.split(":"))
        tz = ZoneInfo(profile.timezone)
    except Exception as exc:
        log.warning("scheduler.invalid_time", error=str(exc))
        hour, minute = 8, 0
        tz = ZoneInfo("UTC")

    # Daily digest
    job_queue.run_daily(
        _daily_digest_job,
        time=dt_time(hour=hour, minute=minute, tzinfo=tz),
        data={"settings": settings},
        name="daily_digest",
    )
    log.info("scheduler.daily_digest_set", time=f"{hour:02d}:{minute:02d}", timezone=str(tz))

    # Deadline reminder check every 6 hours
    job_queue.run_repeating(
        _deadline_reminder_job,
        interval=timedelta(hours=6),
        first=timedelta(minutes=5),
        data={"settings": settings},
        name="deadline_reminders",
    )
    log.info("scheduler.deadline_reminders_set")


async def _daily_digest_job(context: Any) -> None:
    """Job: run scan and send daily digest."""
    settings: Settings = context.job.data["settings"]

    try:
        from todaytech.core.database import get_opportunities, get_session
        from todaytech.core.digest import format_digest
        from todaytech.core.pipeline import run_scan

        result = await run_scan()

        async with get_session() as session:
            opportunities = await get_opportunities(session, limit=50)

        digest = format_digest(
            opportunities,
            unhealthy_sources=result.get("unhealthy_sources", []),
        )

        # Send to user (with plain-text fallback on markdown error)
        try:
            await context.bot.send_message(
                chat_id=settings.telegram_user_id,
                text=digest,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            await context.bot.send_message(
                chat_id=settings.telegram_user_id,
                text=digest,
                disable_web_page_preview=True,
            )
        log.info("scheduler.digest_sent")

    except Exception as exc:
        log.error("scheduler.digest_failed", error=str(exc))


async def _deadline_reminder_job(context: Any) -> None:
    """Job: send deadline reminders at 7, 3, and 1 day(s) before."""
    settings: Settings = context.job.data["settings"]

    try:
        from sqlalchemy import select

        from todaytech.core.database import OpportunityRow, get_session

        today = date.today()
        reminder_days = [7, 3, 1]

        async with get_session() as session:
            # Get saved opportunities with upcoming deadlines
            stmt = (
                select(OpportunityRow)
                .where(OpportunityRow.deadline >= today)
                .where(OpportunityRow.deadline <= today + timedelta(days=8))
                .order_by(OpportunityRow.deadline.asc())
            )
            result = await session.execute(stmt)
            opps = list(result.scalars().all())

        reminders: list[str] = []
        for opp in opps:
            if opp.deadline:
                days_left = (opp.deadline - today).days
                if days_left in reminder_days:
                    emoji = "🔴" if days_left <= 1 else "🟡" if days_left <= 3 else "🟢"
                    reminders.append(
                        f"{emoji} **{opp.title}** — {days_left} day(s) left! "
                        f"(Deadline: {opp.deadline})\n"
                        f"   🆔 `{opp.external_id[:8]}`"
                    )

        if reminders:
            text = "⏰ **Deadline Reminders**\n\n" + "\n\n".join(reminders)
            try:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            log.info("scheduler.reminders_sent", count=len(reminders))

    except Exception as exc:
        log.error("scheduler.reminders_failed", error=str(exc))
